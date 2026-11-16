"""On-demand cloud previews, bounded CLI transport, no content cache or DB writes."""
from pathlib import Path
import sys
import threading
from datetime import datetime, timezone

# Same repository bridge as instruction_diff; deployment may omit box-config-sync.
_PACKAGE_ROOT = Path(__file__).resolve().parents[3] / "box-config-sync"
if str(_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_ROOT))


def snapshot_rows(path):
    try:
        from boxsync.safe_io import read_file, UnsafeIO
        from boxsync.snapshot import validate_snapshot, MAX_SNAPSHOT_BYTES
        from boxsync.validation import parse_json
        value = read_file(path.parent, path.name, max_bytes=MAX_SNAPSHOT_BYTES)
        if value.state != "observed":
            return []
        doc = parse_json(value.data)
        validate_snapshot(doc)
        return doc["domains"]["cloud_skills"]
    except ImportError:
        return []
    except (OSError, ValueError, UnsafeIO):
        return []


class CloudSkillReader:
    def __init__(self, runner):
        self.runner = runner
        self._guard = threading.Lock()
        self._workspaces = {}

    def read(self, workspace, skill, *, file="SKILL.md", tree=False):
        try:
            from boxsync.multica_cli import CliRunner
            from boxsync.domains.cloud_skills import read_body
        except ImportError:
            return {"ok": False, "state": "uncollected", "source": "cloud",
                    "message": "云端正文未采集（读取模块不可用）", "collected_at": None}
        with self._guard:
            if workspace not in self._workspaces:
                self._workspaces[workspace] = (threading.Lock(), CliRunner(
                    binary=self.runner.binary, profile=self.runner.profile, timeout=30, interval=1,
                    server_url=self.runner.server_url, env_provider=self.runner._env))
            lock, cli = self._workspaces[workspace]
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        from .cli_runner import CliError
        from boxsync.domains.cloud_skills import body_fields
        with lock:
            try:
                metadata, texts = read_body(cli, workspace, skill, now)
            except CliError:
                metadata, texts = {**body_fields("error"), "collected_at": now}, {}
        state = metadata["body_state"]
        result = {"ok": False, "source": "cloud", "state": state, **metadata,
                  "evidence_state": "observed" if metadata["body_sha256"] else "unknown",
                  "bytes": metadata["body_bytes"], "sha256": metadata["body_sha256"]}
        messages = {"uncollected": "云端正文未采集", "empty": "正文为空（已读取）",
                    "denied": "读取失败：denied", "error": "读取失败：error",
                    "too_large": "超过 512 KiB 上限", "withheld_secret": "疑似含凭据，已隐藏"}
        if state in messages:
            result["message"] = messages[state]
            if state == "too_large" and metadata["body_bytes"] is not None:
                result["message"] += f"（{metadata['body_bytes']} 字节）"
        # A withheld main body never exposes any text through tree/file requests.
        if state not in ("observed", "empty"):
            return result
        if tree:
            return {**result, "ok": True, "state": "ok", "truncated": metadata["files_truncated"]}
        if file not in texts:
            entry = next((f for f in metadata["files"] if f["path"] == file), None)
            return {**result, "state": "too_large" if entry and entry["state"] == "too_large" else "denied",
                    "bytes": entry["bytes"] if entry else None, "message": "文件不可预览"}
        import hashlib
        raw = texts[file].encode("utf-8")
        return {**result, "ok": True, "state": "ok" if raw else "empty", "content": texts[file],
                "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(), "file": file,
                "format": "markdown" if file.lower().endswith(".md") else "text"}
