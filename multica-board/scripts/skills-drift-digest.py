#!/usr/bin/env python3
"""Read-only Skills digest. --input FILE (or -) is offline; --url uses GET only.

Prints a summary to stdout, never executes suggestions or writes a report/cache.
Live input is limited to a local Board /api/skills endpoint, without credentials,
redirects or proxies. Examples (from the repository root):
  python3 multica-board/scripts/skills-drift-digest.py --input payload.json
  python3 multica-board/scripts/skills-drift-digest.py --url http://127.0.0.1:8765/api/skills --format text
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

MAX_BYTES = 8 * 1024 * 1024
DIGEST_COMMAND = "python3 multica-board/scripts/skills-drift-digest.py --url http://127.0.0.1:8765/api/skills"
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def safe_name(value):
    """Only a bounded skill identifier leaves the payload; never echo raw errors."""
    if (not isinstance(value, str) or len(value) > 100
            or not re.fullmatch(r"[\w.:-]+", value, re.ASCII)
            or re.search(r"(?i)(?:token|secret|password|bearer|sk-|ghp_|github_pat_)", value)):
        return "[redacted]"
    return value


def signals(item):
    return {
        "mismatch": (item.get("hash_compare") or {}).get("status") == "mismatch",
        "drift": item.get("reconciliation_status") in {"modified", "missing", "version_changed"},
        "unrecorded": (item.get("source_class") == "unrecorded" or item.get("origin") == "unrecorded"
                       or any(entry.get("registration") == "unrecorded" for entry in item.get("entries", []))),
        "upgradable": item.get("upgrade_status") == "update" or item.get("bucket") == "upgradable",
    }


def suggestions(item):
    state = signals(item)
    if not any(state.values()):
        return []
    commands = [DIGEST_COMMAND]
    if state["drift"] or state["unrecorded"]:
        commands.append("python3 skills-governance/scripts/drift_report.py --format text")
    for install in item.get("workspace_installs") or []:
        ws, skill = install.get("workspace_id"), install.get("id")
        if not all(isinstance(value, str) and UUID.fullmatch(value) for value in (ws, skill)):
            continue
        command = f"multica --workspace-id {ws} skill get {skill} --output json"
        if command not in commands:
            commands.append(command)
    return commands


def gate_text(gate):
    if not isinstance(gate, dict):
        return "unknown（未采集，不代表通过）"
    state, result = gate.get("state"), gate.get("result")
    if state == "fallback" and result in {"pass", "block"}:
        return f"fallback / {result}（仅本地风险信号，不代表 SkilLock 通过）"
    if state == "recorded" and result in {"pass", "block"}:
        return f"recorded / {result}（历史记录，不代表当前内容通过）"
    return {"empty": "empty（无门禁记录，不代表通过）",
            "error": "error（记录不可读，不代表通过）"}.get(state, "unknown（信息不足，不代表通过）")


def digest(payload, fmt="markdown"):
    if (not isinstance(payload, dict) or payload.get("ok") is not True
            or not isinstance(payload.get("items"), list)
            or not all(isinstance(item, dict) for item in payload["items"])):
        raise ValueError("invalid or unavailable Skills payload")
    rows = [(item, signals(item)) for item in payload["items"]]
    heading = "## " if fmt == "markdown" else ""
    lines = [heading + "Skills drift digest", "只读建议；不自动执行，不写回，不 activate。",
             "计数按条目独立统计，可重叠；hash mismatch 不判断哪份更新。",
             ", ".join(f"{key}={sum(state[key] for _, state in rows)}"
                       for key in ("mismatch", "drift", "unrecorded", "upgradable"))]
    attention = payload.get("attention") or {}
    upgrade_source = next((source for source in (payload.get("meta") or {}).get("sources", [])
                           if source.get("id") == "upgrade"), {})
    upgrade_state = upgrade_source.get("state")
    lines.append("upgrade_source=" + (upgrade_state if upgrade_state in {"ok", "error", "absent", "deferred", "partial"} else "unknown")
                 + "（候选数不代表完整上游检查）")
    lines.append("last_gate=" + gate_text(attention.get("last_gate")))
    for item, state in rows:
        flags = [key for key, value in state.items() if value]
        if not flags:
            continue
        lines.extend(["", f"- {safe_name(item.get('name'))}: {', '.join(flags)}"])
        commands = suggestions(item)
        if fmt == "markdown":
            lines.extend(["```sh", *commands, "```"])
        else:
            lines.extend("  " + command for command in commands)
    if not any(any(state.values()) for _, state in rows):
        lines.append("无需命令 / 信息不足：本次 payload 无可确认的漂移信号，不代表全部通过。")
    lines.append("refresh / update 会写入，须另行批准后人工处理；未生成缺少有效 Workspace ID 的命令。")
    return "\n".join(lines) + "\n"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        raise ValueError("redirects are disabled")


def read_url(url):
    parsed = urlsplit(url)
    if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "::1", "localhost"}
            or parsed.path != "/api/skills" or parsed.username is not None
            or parsed.password is not None or parsed.query or parsed.fragment):
        raise ValueError("use a credential-free local Board /api/skills URL")
    opener = build_opener(ProxyHandler({}), NoRedirect())
    with opener.open(Request(url, method="GET", headers={"Accept": "application/json"}), timeout=10) as response:
        return response.read(MAX_BYTES + 1)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", help="offline JSON file, or - for stdin")
    source.add_argument("--url", help="local Board http://127.0.0.1:PORT/api/skills; GET only")
    parser.add_argument("--format", choices=("markdown", "text"), default="markdown")
    args = parser.parse_args(argv)
    try:
        if args.url:
            raw = read_url(args.url)
        elif args.input == "-":
            raw = sys.stdin.buffer.read(MAX_BYTES + 1)
        else:
            with Path(args.input).open("rb") as handle:
                raw = handle.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("oversized payload")
        output = digest(json.loads(raw), args.format)
    except Exception:
        # Inputs/paths/URLs/errors can contain credentials: report a fixed error.
        print("Skills digest unavailable: check input schema or local GET endpoint; no data changed.", file=sys.stderr)
        return 1
    sys.stdout.write(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
