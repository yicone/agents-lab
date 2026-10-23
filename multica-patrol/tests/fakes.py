"""Offline test fixture: a fake `multica` CLI backend.

IMPORTANT: every object built here is a synthetic test fixture. Nothing in this
file represents, or must ever be presented as, output of a natural hourly patrol
run.
"""

from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path
from datetime import datetime, timedelta, timezone

import patrol.__main__ as patrol_main
from patrol import config

SGT = timezone(config.SGT_OFFSET)
BASE_NOW = datetime(2026, 10, 22, 13, 0, 0, tzinfo=timezone.utc)  # 21:00 SGT

GOOD_ENV = {
    "MULTICA_TASK_ID": "task-fixture-1",
    "MULTICA_AGENT_ID": config.DISPATCHER_AGENT_ID,
    "MULTICA_TOKEN": "mat_TESTTOKEN0123456789abcdef",
    "MULTICA_WORKSPACE_ID": config.WORKSPACE_ID,
    "MULTICA_SERVER_URL": "https://example.invalid",
}


def iso(dt: datetime) -> str:
    return dt.isoformat()


def make_issue(identifier: str, status: str, *, uid: str | None = None,
               assignee_id: str | None = "agent-other",
               created_at: datetime | None = None, updated_at: datetime | None = None,
               last_activity_at: datetime | None = None, labels=None,
               parent_issue_id=None, title="fixture title"):
    uid = uid or f"uuid-{identifier}"
    created = created_at or (BASE_NOW - timedelta(days=10))
    updated = updated_at or created
    return {
        "id": uid, "identifier": identifier, "number": int(identifier.split("-")[1]),
        "status": status, "assignee_id": assignee_id, "assignee_type": "agent" if assignee_id else None,
        "parent_issue_id": parent_issue_id,
        "created_at": iso(created), "updated_at": iso(updated),
        "last_activity_at": iso(last_activity_at or updated),
        "labels": labels or [], "project_id": "proj", "title": title,
    }


def status_changed(to_status: str, at: datetime) -> dict:
    return {"action": "status_changed", "actor_type": "member", "actor_id": "u1",
            "created_at": iso(at), "details": {"to": to_status}}


def comment(content: str, at: datetime, *, author_type="member", author_id="u1",
            cid=None, ctype="comment") -> dict:
    return {"id": cid or f"c-{abs(hash(content)) % 10**8}", "content": content,
            "author_type": author_type, "author_id": author_id,
            "created_at": iso(at), "type": ctype}


def run(status: str, *, rid=None, attempt=1, max_attempts=3,
        started_at: datetime | None = None,
        completed_at: datetime | None = None, agent_id="agent-other") -> dict:
    return {"id": rid or f"r-{status}-{abs(hash((status, agent_id))) % 10**6}",
            "status": status, "attempt": attempt, "max_attempts": max_attempts,
            "created_at": iso(BASE_NOW - timedelta(hours=5)),
            "started_at": iso(started_at or (BASE_NOW - timedelta(hours=5))),
            "completed_at": iso(completed_at) if completed_at else None,
            "error": None, "agent_id": agent_id, "kind": "assign",
            "trigger_comment_id": None}


class FakeMultica:
    """Routes PatrolCLI subprocess calls to in-memory fixture data."""

    def __init__(self):
        self.issues: list[dict] = []
        self.comments: dict[str, list[dict]] = {}
        self.runs: dict[str, list[dict]] = {}
        self.timelines: dict[str, list[dict]] = {}
        self.wakeups: dict[str, list[dict]] = {}
        self.agents: list[dict] = [{"id": config.DISPATCHER_AGENT_ID, "name": "副调度"}]
        self.calls: list[list[str]] = []
        self.comment_add_count = 0
        self.next_comment_id = 1
        self.now: datetime = BASE_NOW
        # Behaviour switches for failure injection.
        self.fail_handler = None          # fn(argv)->Optional[CompletedProcess] or raise
        self.write_mode = "normal"        # normal|timeout_found|timeout_lost|nonzero
        self.author_type = "agent"
        self.author_id = config.DISPATCHER_AGENT_ID
        self.comment_list_fail_ids: set[str] = set()
        self.mono = 0.0
        self.mono_step = 0.0

    def monotonic(self) -> float:
        value = self.mono
        self.mono += self.mono_step
        return value

    # -- helpers ---------------------------------------------------------
    def add_comment(self, issue_id: str, c: dict) -> None:
        self.comments.setdefault(issue_id, []).append(c)

    def ledger_comments(self) -> list[dict]:
        return self.comments.get(config.LEDGER_ISSUE_ID, [])

    def inbox_comments(self) -> list[dict]:
        return self.comments.get(config.INBOX_ISSUE_ID, [])

    # -- subprocess entry point ------------------------------------------
    def exec(self, argv, **kwargs):
        argv = list(argv)
        self.calls.append(argv)
        if self.fail_handler:
            injected = self.fail_handler(argv)
            if injected is not None:
                return injected
        args = argv[1:]
        words = [a for a in args if not a.startswith("-")]

        def out(data):
            return subprocess.CompletedProcess(argv, 0, json.dumps(data), "")

        def fail(msg="boom", rc=1):
            return subprocess.CompletedProcess(argv, rc, "", msg)

        try:
            if words[:2] == ["issue", "list"]:
                status = args[args.index("--status") + 1]
                limit = int(args[args.index("--limit") + 1])
                offset = int(args[args.index("--offset") + 1])
                matched = [i for i in self.issues if i["status"] == status]
                page = matched[offset:offset + limit]
                return out({"issues": page, "has_more": offset + limit < len(matched),
                            "limit": limit, "offset": offset, "total": len(matched)})
            if words[:2] == ["issue", "get"]:
                issue = next((i for i in self.issues if i["id"] == words[2]), None)
                if issue is None:
                    return fail("not found", 2)
                return out(issue)
            if words[:2] == ["issue", "runs"]:
                runs = self.runs.get(words[2], [])
                if "--active" in args:
                    runs = [r for r in runs if r["status"] in config.ACTIVE_RUN_STATUSES]
                return out(runs)
            if words[:2] == ["issue", "timeline"]:
                return out(self.timelines.get(words[2], []))
            if words[:3] == ["issue", "comment", "list"]:
                issue_id = words[3]
                if issue_id in self.comment_list_fail_ids:
                    return fail("comment list unavailable")
                items = list(self.comments.get(issue_id, []))
                if "--since" in args:
                    since = datetime.fromisoformat(
                        args[args.index("--since") + 1].replace("Z", "+00:00"))
                    items = [c for c in items
                             if datetime.fromisoformat(c["created_at"]) > since]
                if "--roots-only" in args:
                    items = [c for c in items if not c.get("parent")]
                return out(items)
            if words[:3] == ["issue", "wakeup", "create"]:
                issue_id = words[3]
                path = args[args.index("--instruction-file") + 1]
                entry = {"id": f"wake-{len(self.wakeups.get(issue_id, [])) + 1}",
                         "agent_id": args[args.index("--agent-id") + 1],
                         "instruction": Path(path).read_text(), "kind": "at",
                         "created_at": iso(self.now)}
                self.wakeups.setdefault(issue_id, []).append(entry)
                return out(entry)
            if words[:3] == ["issue", "wakeup", "list"]:
                return out(self.wakeups.get(words[3], []))
            if words[:3] == ["issue", "comment", "add"]:
                return self._comment_add(argv, words, args, out, fail)
            if words[:2] == ["agent", "list"]:
                return out(self.agents)
            return fail(f"unhandled fixture command: {' '.join(map(str, argv))}", 2)
        except subprocess.TimeoutExpired:
            raise
        except Exception as exc:  # pragma: no cover - fixture bug surfacing
            return fail(f"fixture error: {exc}", 2)

    def _comment_add(self, argv, words, args, out, fail):
        self.comment_add_count += 1
        issue_id = words[3]
        path = args[args.index("--content-file") + 1]
        with open(path) as fh:
            content = fh.read()
        cid = f"posted-{self.next_comment_id}"
        self.next_comment_id += 1
        if self.write_mode == "timeout_lost":
            raise subprocess.TimeoutExpired(argv, 90)
        if self.write_mode == "nonzero":
            return fail("write failed", 1)
        entry = {"id": cid, "content": content, "author_type": self.author_type,
                 "author_id": self.author_id, "created_at": iso(self.now),
                 "type": "comment"}
        self.comments.setdefault(issue_id, []).append(entry)
        if self.write_mode == "timeout_found":
            raise subprocess.TimeoutExpired(argv, 90)
        return out({"id": cid, "author_type": self.author_type, "author_id": self.author_id})


def invoke(fake: FakeMultica, argv: list[str], *, env=None, now=None, state_dir):
    """Run patrol main() fully offline. Returns (exit_code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    code = patrol_main.main(
        argv,
        env=dict(GOOD_ENV if env is None else env),
        exec_fn=fake.exec,
        sleep_fn=lambda _s: None,
        now_fn=lambda: (now or fake.now),
        monotonic_fn=fake.monotonic,
        out=out, err=err,
        state_dir=state_dir,
    )
    return code, out.getvalue(), err.getvalue()


def last_result(stdout: str) -> dict:
    line = [ln for ln in stdout.splitlines() if ln.startswith("PATROL_RESULT ")][-1]
    return json.loads(line[len("PATROL_RESULT "):])
