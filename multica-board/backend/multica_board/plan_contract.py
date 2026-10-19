"""G1 plan schema, restricted JCS domain and deterministic read-only decisions.

Schema 1 intentionally permits only strings, null, booleans and safe integers:
no floating point serialization is needed for RFC 8785 interoperability.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit
from uuid import UUID

FIELDS = ("runtime_id", "model", "thinking_level", "service_tier")
G1 = ("activity", "permission", "capability", "compatibility", "health")
G2 = ("approval_claim", "commit_fence", "cas_activity_guard", "operation_attribution")
HASH_ALGO = "sha256-jcs-schema1"
REASONS = {
    "SKIP_ACTIVE": "存在活动任务",
    "NO_CHANGE": "无变化",
    "BLOCKED_SOURCE": "源字段不完整",
    "BLOCKED_ARCHIVED": "已归档",
    "BLOCKED_TARGET": "目标不可确认",
    "BLOCKED_ACTIVITY_COMPLETENESS": "活动未穷尽",
    "BLOCKED_PERMISSION": "权限未证实",
    "BLOCKED_CAPABILITY": "能力未证实",
    "BLOCKED_CONFIG_COMPATIBILITY": "配置兼容未证实",
    "BLOCKED_HEALTH": "目标健康未证实",
    "BLOCKED_HISTORICAL": "历史快照仅供审阅",
}
ACTIVE = {"queued", "dispatched", "running", "waiting_local_directory"}
TERMINAL = {"completed", "failed", "cancelled"}


def uuid(value: str) -> str:
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ValueError("expected lowercase canonical UUID")
    return value


def utc(value: str) -> datetime:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", value):
        raise ValueError("expected UTC timestamp YYYY-MM-DDTHH:MM:SSZ")
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def server_identity(value: str) -> str:
    p = urlsplit(value)
    if p.scheme not in {"https", "http"} or not p.hostname or p.username or p.password or p.query or p.fragment:
        raise ValueError("server must be a credential-free HTTP(S) origin")
    if p.path not in {"", "/"}:
        raise ValueError("server must be an origin without a path")
    host = p.hostname.lower()
    if p.scheme == "http" and host not in {"127.0.0.1", "::1"}:
        raise ValueError("HTTP server must be loopback")
    host = f"[{host}]" if ":" in host else host.encode("idna").decode()
    port = p.port
    suffix = f":{port}" if port and port != {"https": 443, "http": 80}[p.scheme] else ""
    return f"{p.scheme}://{host}{suffix}"


def canonical(value) -> bytes:
    def encode(v):
        if v is None or isinstance(v, bool):
            return json.dumps(v)
        if type(v) is int and abs(v) <= 9007199254740991:
            return str(v)
        if isinstance(v, str):
            v.encode("utf-8")  # reject lone surrogates
            return json.dumps(v, ensure_ascii=False, separators=(",", ":"))
        if isinstance(v, list):
            return "[" + ",".join(encode(x) for x in v) + "]"
        if isinstance(v, dict) and all(isinstance(k, str) for k in v):
            keys = sorted(v, key=lambda k: k.encode("utf-16-be"))
            return "{" + ",".join(encode(k) + ":" + encode(v[k]) for k in keys) + "}"
        raise ValueError("schema 1 JCS domain excludes floats and unsafe integers")
    return encode(value).encode("utf-8")


def strict_json(raw: bytes):
    def pairs(items):
        out = {}
        for k, v in items:
            if k in out:
                raise ValueError("duplicate JSON key")
            out[k] = v
        return out
    def invalid(_):
        raise ValueError("non-finite JSON number")
    result = json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)
    canonical(result)
    return result


def exact(obj, keys):
    if not isinstance(obj, dict) or set(obj) != set(keys):
        raise ValueError("invalid schema fields")


def text(value, limit=200):
    if not isinstance(value, str) or len(value) > limit or any(ord(c) < 32 for c in value):
        raise ValueError("invalid text field")
    value.encode("utf-8")


def nullable(value):
    if value is not None:
        text(value)


def validate_payload(p):
    exact(p, ("schema_version", "plan_id", "mode", "server_identity", "actor", "on_behalf_of",
              "workspace_allow_list", "selected_agents", "generated_at", "expires_at", "policy_version",
              "snapshot_kind", "selection_reason", "allowed_fields", "rows", "counts"))
    if type(p["schema_version"]) is not int or p["schema_version"] != 1:
        raise ValueError("unsupported schema")
    uuid(p["plan_id"])
    if p["mode"] != "review_only" or p["policy_version"] != "g1-v1":
        raise ValueError("G1 accepts review_only / g1-v1 only")
    if server_identity(p["server_identity"]) != p["server_identity"]:
        raise ValueError("noncanonical server identity")
    exact(p["actor"], ("type", "id", "verified"))
    if p["actor"]["type"] not in {"member", "agent"} or p["actor"]["verified"] is not False:
        raise ValueError("G1 actor is an unverified declaration")
    uuid(p["actor"]["id"])
    uuid(p["on_behalf_of"])
    if not 0 < (utc(p["expires_at"]) - utc(p["generated_at"])).total_seconds() <= 600:
        raise ValueError("plan TTL must be 1..600 seconds")
    if p["snapshot_kind"] not in {"live_cli", "historical"} or p["allowed_fields"] != list(FIELDS):
        raise ValueError("invalid snapshot or allowed fields")
    text(p["selection_reason"])
    if not isinstance(p["workspace_allow_list"], list) or len(p["workspace_allow_list"]) != 1:
        raise ValueError("schema 1 requires exactly one workspace")
    ws = []
    for w in p["workspace_allow_list"]:
        exact(w, ("id", "name"))
        ws.append(uuid(w["id"]))
        nullable(w["name"])
    if not ws or ws != sorted(set(ws)):
        raise ValueError("allow-list must be nonempty sorted unique UUIDs")
    selected = p["selected_agents"]
    if not isinstance(selected, list) or not 1 <= len(selected) <= 1000:
        raise ValueError("select 1..1000 agents")
    keys = []
    for s in selected:
        exact(s, ("workspace_id", "agent_id"))
        keys.append((uuid(s["workspace_id"]), uuid(s["agent_id"])))
    if keys != sorted(set(keys)) or any(w not in ws for w, _ in keys):
        raise ValueError("invalid selected agent scope")
    if not isinstance(p["rows"], list) or len(p["rows"]) != len(keys):
        raise ValueError("each selected agent needs one row")
    seen = []
    for r in p["rows"]:
        exact(r, ("workspace_id", "agent_id", "agent_name", "archived", "fields", "source_version",
                  "source_fingerprint", "observed_at", "source_runtime", "target_runtime", "activity", "checks", "decision", "reasons", "impact"))
        seen.append((uuid(r["workspace_id"]), uuid(r["agent_id"])))
        nullable(r["agent_name"])
        if r["archived"] is not None and type(r["archived"]) is not bool:
            raise ValueError("invalid archived flag")
        nullable(r["source_version"])
        utc(r["observed_at"])
        if r["observed_at"] != p["rows"][0]["observed_at"]:
            raise ValueError("rows must share the snapshot observation time")
        exact(r["fields"], FIELDS)
        for key, side in (("source_runtime", "old"), ("target_runtime", "new")):
            runtime = r[key]
            if runtime is not None:
                exact(runtime, ("id", "name", "provider", "status", "last_seen_at"))
                uuid(runtime["id"])
                if runtime["id"] != r["fields"]["runtime_id"][side]:
                    raise ValueError("runtime metadata identity mismatch")
                for k in ("name", "provider", "status"):
                    nullable(runtime[k])
                if runtime["last_seen_at"] is not None:
                    utc(runtime["last_seen_at"])
        for f, d in r["fields"].items():
            exact(d, ("op", "old", "new", "old_known"))
            nullable(d["old"])
            nullable(d["new"])
            if type(d["old_known"]) is not bool or d["op"] not in {"omit", "set", "clear"}:
                raise ValueError("invalid field operation")
            if d["op"] == "omit" and d["new"] != d["old"]:
                raise ValueError("omit must retain raw old value")
            if d["op"] == "clear" and (f == "runtime_id" or d["new"] is not None):
                raise ValueError("clear must mean null for optional fields")
            if d["op"] == "set" and (not isinstance(d["new"], str) or not d["new"] or d["new"] != d["new"].strip()):
                raise ValueError("set requires nonblank trimmed text")
            if f == "runtime_id":
                if d["old"] not in (None, ""):
                    uuid(d["old"])
                if d["new"] not in (None, ""):
                    uuid(d["new"])
        source = {f: {"value": d["old"], "known": d["old_known"]} for f, d in r["fields"].items()}
        if r["source_fingerprint"] != hashlib.sha256(canonical(source)).hexdigest():
            raise ValueError("invalid source fingerprint")
        exact(r["checks"], G1)
        for check in r["checks"].values():
            exact(check, ("status", "source", "observed_at"))
            if check["status"] not in {"PASS", "FAIL", "UNKNOWN"}:
                raise ValueError("invalid check status")
            text(check["source"])
            utc(check["observed_at"])
        if any(r["checks"][k]["status"] != "UNKNOWN" for k in ("activity", "permission", "capability", "compatibility")):
            raise ValueError("G1 cannot assert unproven read capabilities")
        if p["snapshot_kind"] == "historical" and (
                r["checks"]["health"]["status"] != "UNKNOWN" or
                r["checks"]["health"]["source"] != "historical_snapshot"):
            raise ValueError("historical health must be UNKNOWN with historical provenance")
        a = r["activity"]
        exact(a, ("pages_read", "tasks_seen", "completeness", "source", "observed_at", "runs"))
        for k in ("pages_read", "tasks_seen"):
            if type(a[k]) is not int or not 0 <= a[k] <= 1000000:
                raise ValueError("invalid activity count")
        if a["completeness"] != "UNKNOWN":
            raise ValueError("CLI pagination completeness has not been proven")
        text(a["source"])
        utc(a["observed_at"])
        if not isinstance(a["runs"], list) or len(a["runs"]) > 200:
            raise ValueError("invalid runs")
        if a["tasks_seen"] != len(a["runs"]):
            raise ValueError("task count mismatch")
        for run in a["runs"]:
            exact(run, ("status", "created_at"))
            text(run["status"])
            if run["created_at"] is not None:
                utc(run["created_at"])
        if not isinstance(r["impact"], list) or len(r["impact"]) > 4:
            raise ValueError("invalid impact")
        for note in r["impact"]:
            text(note)
        if (r["decision"], r["reasons"]) != decide(r, p["snapshot_kind"]):
            raise ValueError("decision does not match evidence")
    exact(p["counts"], ("APPROVABLE", "BLOCKED", "SKIP_ACTIVE", "NO_CHANGE"))
    if any(type(n) is not int for n in p["counts"].values()) or seen != keys or p["counts"] != counts(p["rows"]):
        raise ValueError("row scope/count mismatch")
    canonical(p)


def decide(row, snapshot_kind):
    if any(r["status"] in ACTIVE for r in row["activity"]["runs"]):
        return "SKIP_ACTIVE", ["SKIP_ACTIVE"]
    reasons = []
    if not all(d["old_known"] for d in row["fields"].values()):
        reasons.append("BLOCKED_SOURCE")
    if row["archived"]:
        reasons.append("BLOCKED_ARCHIVED")
    if snapshot_kind == "historical":
        reasons.append("BLOCKED_HISTORICAL")
    for key, code in zip(G1, ("BLOCKED_ACTIVITY_COMPLETENESS", "BLOCKED_PERMISSION", "BLOCKED_CAPABILITY",
                             "BLOCKED_CONFIG_COMPATIBILITY", "BLOCKED_HEALTH")):
        if row["checks"][key]["status"] != "PASS":
            reasons.append(code)
    if not row["fields"]["runtime_id"]["new"] or row["checks"]["capability"]["source"] == "target_registration_not_confirmed":
        reasons.append("BLOCKED_TARGET")
    if reasons:
        return "BLOCKED", reasons
    if all(d["old_known"] and d["old"] == d["new"] for d in row["fields"].values()):
        return "NO_CHANGE", ["NO_CHANGE"]
    return "APPROVABLE", []


def counts(rows):
    out = {k: 0 for k in ("APPROVABLE", "BLOCKED", "SKIP_ACTIVE", "NO_CHANGE")}
    for r in rows:
        out[r["decision"]] += 1
    return out


def envelope(payload):
    validate_payload(payload)
    return {"payload": payload, "hash_algo": HASH_ALGO,
            "plan_hash": hashlib.sha256(canonical(payload)).hexdigest()}
