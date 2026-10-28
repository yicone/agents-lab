"""Usage snapshot, quota signals, and the read-only /api/usage view."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from multica_board.__main__ import DEFAULT_PROFILE, main  # noqa: E402
from multica_board.cli_runner import CliRunner  # noqa: E402
from multica_board.collector import CollectOptions, norm_task  # noqa: E402
from multica_board.redact import classify_error  # noqa: E402
from multica_board.store import Store  # noqa: E402
from multica_board.usage import (  # noqa: E402
    USAGE_NOTE,
    UsageCollector,
    build_usage_response,
    evaluate_signal,
    extract_provider,
    run_codexbar,
    write_snapshot,
)
from multica_board.views import ATTENTION_TEXT_BYTES, Views, render_attention_text  # noqa: E402

SERVER = "usage-test"
NOW = datetime(2026, 10, 16, 16, 43, tzinfo=timezone.utc)
DAY = "2026-10-16T12:00:00Z"
SECRET = "super-secret-token-value"
EMAIL = "person@example.com"


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


class RunCodexbarTests(unittest.TestCase):
    NOTICE = "A new version of Grok Build is available: 1.2.3"

    def test_stdout_json_error_takes_priority(self):
        message = "Grok web billing requires a signed-in grok.com browser session or `grok login`"
        for payload in ({"error": message}, [{"error": {"message": message}}], {"message": message}):
            with self.subTest(payload=payload):
                def execute(argv, **kwargs):
                    self.assertEqual(argv, ["codexbar", "usage", "--provider", "grok", "--format", "json", "--no-color"])
                    return subprocess.CompletedProcess(argv, 1, json.dumps(payload), self.NOTICE)
                with self.assertRaises(RuntimeError) as caught:
                    run_codexbar("codexbar", "grok", exec_=execute)
                self.assertEqual(str(caught.exception), message)

    def test_upgrade_only_stderr_falls_back_to_exit_code(self):
        for stdout in ("", "not JSON", "[]"):
            with self.subTest(stdout=stdout):
                with self.assertRaisesRegex(RuntimeError, "^exit code 7$"):
                    run_codexbar("codexbar", "grok", exec_=lambda argv, **kw: subprocess.CompletedProcess(argv, 7, stdout, self.NOTICE))

    def test_stderr_preserves_real_error_and_redacts(self):
        with self.assertRaises(RuntimeError) as caught:
            run_codexbar("codexbar", "grok", exec_=lambda argv, **kw: subprocess.CompletedProcess(argv, 1, "", self.NOTICE + "\nlogin failed for " + EMAIL))
        self.assertEqual(str(caught.exception), "login failed for [redacted]")

    def test_success_returns_unchanged_payload(self):
        payload = [{"provider": "grok", "usage": {"primary": {"usedPercent": 42}}}]
        result = run_codexbar("codexbar", "grok", exec_=lambda argv, **kw: subprocess.CompletedProcess(argv, 0, json.dumps(payload), self.NOTICE))
        self.assertEqual(result, payload)


class ClassifyTests(unittest.TestCase):
    def test_failure_reason_overrides_only_weak_kinds(self):
        self.assertEqual(classify_error("something odd", "agent_error.provider_quota_limit"), "quota")
        self.assertEqual(
            classify_error("model gpt-9 not found", "agent_error.provider_capacity_or_rate_limit"),
            "rate_limit",
        )
        self.assertEqual(classify_error(None, "provider_capacity"), "rate_limit")
        self.assertEqual(classify_error(None, "cancelled"), "cancelled")
        self.assertEqual(classify_error("hermes process exited", "agent_error.provider_quota_limit"), "process")
        self.assertIsNone(classify_error(None, None))

    def test_norm_task_keeps_reason_codes_only(self):
        row = norm_task({
            "id": "t",
            "status": "failed",
            "error": "Selected model is at capacity. Please try a different model.",
            "failure_reason": "agent_error.model_not_found_or_unavailable",
        })
        self.assertEqual(row["error_kind"], "rate_limit")
        self.assertEqual(row["failure_reason"], "agent_error.model_not_found_or_unavailable")
        leaked = norm_task({
            "id": "t2",
            "status": "failed",
            "error": "something odd",
            "failure_reason": f"see {EMAIL}",
        })
        self.assertIsNone(leaked["failure_reason"])
        self.assertEqual(leaked["error_kind"], "unknown")


class SignalTests(unittest.TestCase):
    def test_rules(self):
        pace_ok = {"will_last_to_reset": True, "delta_percent": -12}
        self.assertEqual(evaluate_signal(79.1, 43200, 66.9, pace_ok)[0], "normal")
        self.assertEqual(evaluate_signal(85, 10080, 10, {"will_last_to_reset": False, "delta_percent": -1})[0], "exhausting")
        self.assertEqual(evaluate_signal(85, 10080, 10, {"will_last_to_reset": True, "delta_percent": 15})[0], "exhausting")
        self.assertEqual(evaluate_signal(92, 43200, 1.5, None)[0], "normal")
        self.assertEqual(evaluate_signal(10, 10080, 48, None)[0], "dispatchable")
        self.assertEqual(evaluate_signal(10, 10080, 48.1, None)[0], "normal")
        self.assertEqual(evaluate_signal(10, 43200, 72, None)[0], "dispatchable")
        self.assertEqual(evaluate_signal(71, 43200, 72, None)[0], "normal")

    def test_extract_strips_identity_and_reads_codex_secondary(self):
        cursor = extract_provider("cursor", [{
            "source": "web",
            "rateWindowLabels": {"primary": "Total"},
            "pace": {"primary": {"expectedUsedPercent": 91, "deltaPercent": -54, "willLastToReset": True, "stage": "farBehind", "summary": EMAIL}},
            "usage": {
                "accountEmail": EMAIL,
                "accountOrganization": "Secret Org",
                "loginMethod": "google",
                "identity": {"accountEmail": EMAIL, "accountID": "acc_SHOULD_NOT_LEAK", "providerID": "p"},
                "providerCost": {"limit": "secret-cost"},
                "updatedAt": "2026-10-16T16:43:36Z",
                "primary": {"usedPercent": 36.43, "windowMinutes": 43200, "resetsAt": "2026-10-19T11:37:19Z"},
                "extraRateWindows": [{
                    "title": "Grok Bot",
                    "id": "grok-bot-secret-id",
                    "window": {"usedPercent": 46.26, "windowMinutes": 10080, "resetsAt": "2026-10-21T07:38:31Z", "id": "win-id"},
                }],
            },
            "credits": {"remaining": 1, "events": [{"id": "evt-secret"}]},
        }])
        blob = json.dumps(cursor)
        for secret in (EMAIL, "acc_SHOULD_NOT_LEAK", "secret-cost", "grok-bot-secret-id", "evt-secret", "Secret Org"):
            self.assertNotIn(secret, blob, secret)
        self.assertEqual([w["key"] for w in cursor["windows"]], ["primary", "extra:grok_bot"])
        self.assertNotIn("credits", cursor)

        codex = extract_provider("codex", {
            "source": "oauth",
            "rateWindowLabels": {"secondary": "Weekly"},
            "usage": {
                "accountEmail": EMAIL,
                "primary": None,
                "secondary": {"usedPercent": 0, "windowMinutes": 10080, "resetsAt": "2026-10-23T07:33:54Z"},
                "updatedAt": "2026-10-16T00:00:00Z",
            },
        })
        self.assertEqual([w["key"] for w in codex["windows"]], ["secondary"])
        self.assertNotIn(EMAIL, json.dumps(codex))


class _FakeCli:
    def __init__(self):
        self.calls: list[list[str]] = []

    def __call__(self, argv, **_kwargs):
        self.calls.append(list(argv))
        if "workspace" in argv and "list" in argv:
            data = [{"id": "ws-a", "slug": "alpha", "name": "Alpha"}]
        elif "usage" in argv:
            rid = argv[argv.index("usage") + 1]
            data = [{
                "workspace_id": "ws-a",
                "runtime_id": rid,
                "date": "2026-10-16",
                "provider": "hermes",
                "model": "hidden-model",
                "input_tokens": 10,
                "output_tokens": 2,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
                "cost_usd_ticks": 0,
            }]
        elif "activity" in argv:
            data = [{"workspace_id": "ws-a", "hour": 1, "count": 4}, {"workspace_id": "ws-a", "hour": 99, "count": 1}]
        else:
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="unexpected")
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(data), stderr="")


class _FakeCodexbar:
    def __call__(self, argv, **_kwargs):
        provider = argv[argv.index("--provider") + 1]
        if provider == "grok":
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr=f"token={SECRET} {EMAIL}")
        if provider == "codex":
            payload = {
                "source": "oauth",
                "rateWindowLabels": {"secondary": "Weekly"},
                "usage": {
                    "accountEmail": EMAIL,
                    "primary": None,
                    "secondary": {"usedPercent": 0, "windowMinutes": 10080, "resetsAt": "2026-10-23T07:33:54Z"},
                    "updatedAt": "2026-10-16T01:00:00Z",
                },
            }
        else:
            payload = {
                "source": "web",
                "rateWindowLabels": {"primary": "Total"},
                "pace": {"primary": {"expectedUsedPercent": 91, "deltaPercent": -54, "willLastToReset": True, "stage": "farBehind"}},
                "usage": {
                    "accountEmail": EMAIL,
                    "identity": {"accountID": "acc_SHOULD_NOT_LEAK"},
                    "providerCost": {"limit": "secret-cost"},
                    "updatedAt": "2026-10-16T16:43:36Z",
                    "primary": {"usedPercent": 36.43, "windowMinutes": 43200, "resetsAt": "2026-10-19T11:37:19Z"},
                    "extraRateWindows": [{
                        "title": "Grok Bot",
                        "id": "grok-bot-secret-id",
                        "window": {"usedPercent": 46.26, "windowMinutes": 10080, "resetsAt": "2026-10-21T07:38:31Z"},
                    }],
                },
            }
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps([payload]), stderr="")


class CollectTests(unittest.TestCase):
    def test_stdout_error_reaches_logs_and_collection_result(self):
        message = "Grok web billing requires a signed-in grok.com browser session or `grok login`"

        def execute(argv, **kwargs):
            if argv[argv.index("--provider") + 1] == "grok":
                return subprocess.CompletedProcess(argv, 1, json.dumps([{"error": {"message": message}}]), RunCodexbarTests.NOTICE)
            return _FakeCodexbar()(argv, **kwargs)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = Store(root / "board.sqlite")
            self._seed(store)
            try:
                collector = UsageCollector(
                    CliRunner(profile=SERVER, _exec=_FakeCli(), retries=0), store, SERVER,
                    CollectOptions(all_workspaces=True),
                    snapshot_path=root / "snapshot.json", history_path=root / "history.jsonl",
                    codexbar_exec=execute, now=NOW,
                )
                with self.assertLogs("multica_board.usage", level="WARNING") as logs:
                    result = collector.collect()
                self.assertFalse(result["ok"])
                self.assertIn("grok: RuntimeError: " + message, result["usage_errors"])
                self.assertIn(message, "\n".join(logs.output))
                self.assertNotIn(RunCodexbarTests.NOTICE, "\n".join(logs.output))
                self.assertNotIn(RunCodexbarTests.NOTICE, json.dumps(result))
            finally:
                store.close()

    # Collection, response building and live views must share the fixture clock.
    @patch("multica_board.views._now", return_value=NOW)
    def test_failure_isolation_counts_and_snapshot(self, _clock):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "board.sqlite"
            snapshot = root / "usage-snapshot.json"
            history = root / "usage-history.jsonl"
            store = Store(db)
            self._seed(store)
            write_snapshot(snapshot, {
                "usage_collected_at": "2026-10-16T00:00:00Z",
                "days": 14,
                "external_quotas": [{
                    "provider": "grok",
                    "status": "ok",
                    "source": "grok-cli-proxy",
                    "updated_at": "2026-10-16T00:00:00Z",
                    "last_success_at": "2026-10-16T00:00:00Z",
                    "error": None,
                    "windows": [{
                        "key": "primary",
                        "label": "Weekly",
                        "window_minutes": 10080,
                        "used_percent": 95,
                        "resets_at": "2026-10-18T16:43:00Z",
                        "pace": None,
                    }],
                }],
                "token_by_runtime": [{
                    "workspace_id": "ws-a",
                    "runtime_id": "rt-old",
                    "provider": "hermes",
                    "name": "Old",
                    "daily": [],
                    "hourly_activity": [],
                }],
            })
            history.write_text(
                json.dumps({"at": "2020-01-01T00:00:00Z", "note": "stale"}) + "\n"
                + json.dumps({"at": "2026-10-15T00:00:00Z", "note": EMAIL}) + "\n",
                encoding="utf-8",
            )
            result = UsageCollector(
                CliRunner(profile=SERVER, _exec=_FakeCli(), _sleep=lambda _s: None, retries=0),
                store,
                SERVER,
                CollectOptions(all_workspaces=True, run_days=14),
                codexbar_bin="codexbar",
                usage_days=14,
                snapshot_path=snapshot,
                history_path=history,
                history_days=90,
                codexbar_exec=_FakeCodexbar(),
                now=NOW,
            ).collect()
            store.close()

            self.assertFalse(result["ok"])
            self.assertEqual(result["providers"]["cursor"], "ok")
            self.assertEqual(result["providers"]["grok"], "error")
            self.assertEqual(result["providers"]["codex"], "ok")
            self.assertEqual(result["runtimes_queried"], 3)
            raw = snapshot.read_text(encoding="utf-8")
            for secret in (SECRET, EMAIL, "acc_SHOULD_NOT_LEAK", "secret-cost", "grok-bot-secret-id"):
                self.assertNotIn(secret, raw, secret)
            self.assertEqual(_mode(snapshot), 0o600)
            self.assertEqual(_mode(history), 0o600)
            self.assertFalse(list(root.glob(".*.tmp")))
            hist = history.read_text(encoding="utf-8")
            self.assertNotIn("2020-01-01", hist)
            self.assertNotIn(EMAIL, hist)
            self.assertIn("[redacted]", hist)

            saved = json.loads(raw)
            grok = next(item for item in saved["external_quotas"] if item["provider"] == "grok")
            self.assertEqual(grok["status"], "error")
            self.assertEqual(grok["last_success_at"], "2026-10-16T00:00:00Z")
            self.assertEqual(grok["windows"][0]["used_percent"], 95)
            self.assertEqual(
                [item["provider"] for item in saved["external_quotas"]],
                ["cursor", "grok", "codex", "9router", "orca"],
            )

            again = Store(db)
            try:
                body = build_usage_response(
                    saved,
                    again.query(
                        "SELECT workspace_id, runtime_id, status, error_kind, failure_reason, "
                        "completed_at, started_at, created_at FROM tasks WHERE server=?",
                        (SERVER,),
                    ),
                    again.query(
                        "SELECT workspace_id, id, name, custom_name, provider FROM runtimes WHERE server=?",
                        (SERVER,),
                    ),
                    again.query("SELECT id, slug, name FROM workspaces WHERE server=?", (SERVER,)),
                    now=NOW,
                )
                live = Views(again, SERVER, app_url="https://app.test", collect_interval_s=60).usage()
            finally:
                again.close()
            self.assertEqual(body["snapshot_state"], "partial")
            self.assertEqual(live["snapshot_state"], "partial")
            self.assertEqual(live["external_quotas"][0]["windows"][0]["signal"], "dispatchable")
            self.assertEqual(live, body)
            self.assertEqual(body["usage_collected_at"], "2026-10-16T16:43:00Z")
            self.assertEqual(body["multica_daily"]["note"], USAGE_NOTE)
            cursor = next(item for item in body["external_quotas"] if item["provider"] == "cursor")
            primary = next(window for window in cursor["windows"] if window["key"] == "primary")
            extra = next(window for window in cursor["windows"] if window["key"] == "extra:grok_bot")
            self.assertEqual(primary["signal"], "dispatchable")
            self.assertEqual(primary["hours_until_reset"], 66.9)
            self.assertEqual(primary["remaining_percent"], 63.57)
            self.assertEqual(extra["signal"], "normal")
            self.assertEqual(extra["hours_until_reset"], 110.9)
            signals = body["signals"]["dispatchable"]
            self.assertTrue(any(item["provider"] == "cursor" and item["window_key"] == "primary" for item in signals))
            grok_view = next(item for item in body["external_quotas"] if item["provider"] == "grok")
            self.assertEqual(grok_view["windows"][0]["signal"], "normal")
            self.assertEqual(grok_view["windows"][0]["band"], "unknown")
            self.assertTrue(grok_view["stale"])
            self.assertEqual(body["signals"]["exhausting"], [])
            unconnected = [item["provider"] for item in body["external_quotas"] if item["status"] == "unconnected"]
            self.assertEqual(unconnected, ["9router", "orca"])

            hermes = next(row for row in body["multica_daily"]["by_provider"] if row["provider"] == "hermes" and row["date"] == "2026-10-16")
            self.assertEqual(hermes["runs_total"], 7)
            self.assertEqual(hermes["completed"], 1)
            self.assertEqual(hermes["cancelled"], 2)
            self.assertEqual(hermes["true_failed"], 1)
            self.assertEqual(hermes["capacity_failed"], 2)
            self.assertEqual(hermes["quota_failed"], 1)
            self.assertEqual(hermes["rate_limit_failed"], 1)
            self.assertEqual(hermes["failed_total"], 3)
            self.assertEqual(hermes["input_tokens"], 30)
            self.assertNotIn("used_percent", json.dumps(body["multica_daily"]))
            self.assertNotIn("input_tokens", json.dumps(body["external_quotas"]))
            ids = {row["runtime_id"] for row in body["multica_daily"]["by_runtime"]}
            self.assertEqual(ids, {"rt1", "rt3", "rt-old"})
            rt1 = next(row for row in body["multica_daily"]["by_runtime"] if row["runtime_id"] == "rt1")
            self.assertEqual(rt1["hourly_activity"], [{"hour": 1, "count": 4}])
            self.assertEqual(rt1["workspace_slug"], "alpha")

            view = Views(Store(db), SERVER, app_url="https://app.test", collect_interval_s=60)
            try:
                attention = view.attention()
            finally:
                view.store.close()
            alert = next(item for item in attention["quota_alerts"] if item["identifier"] == "grok:primary")
            self.assertEqual(alert["status"], "quota_unknown")
            self.assertIsNone(attention["quota_alerts"][0]["agent_name"])
            self.assertIn("额度未知", alert["title"])
            text = render_attention_text(attention)
            self.assertIn("# quota_alerts", text)
            self.assertLessEqual(len(text.encode()), ATTENTION_TEXT_BYTES)
            self.assertNotIn(EMAIL, text)

    def test_queried_runtimes_skip_idle_offline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = Store(root / "board.sqlite")
            self._seed(store)
            fake = _FakeCli()
            UsageCollector(
                CliRunner(profile=SERVER, _exec=fake, _sleep=lambda _s: None, retries=0),
                store,
                SERVER,
                CollectOptions(all_workspaces=True),
                snapshot_path=root / "usage-snapshot.json",
                history_path=root / "usage-history.jsonl",
                codexbar_exec=_FakeCodexbar(),
                now=NOW,
            ).collect()
            store.close()
            queried = [call[call.index("usage") + 1] for call in fake.calls if "usage" in call and "--provider" not in call]
            self.assertEqual(sorted(queried), ["rt1", "rt3"])

    def _seed(self, store: Store) -> None:
        with store.tx() as conn:
            store.upsert(conn, "workspaces", {"server": SERVER, "id": "ws-a", "name": "Alpha", "slug": "alpha"})
            for rid, status, name in (
                ("rt1", "online", "Hermes"),
                ("rt2", "offline", "Idle"),
                ("rt3", "offline", "Recent"),
                ("rt-old", "offline", "Old"),
            ):
                store.upsert(conn, "runtimes", {
                    "server": SERVER, "workspace_id": "ws-a", "id": rid, "name": name,
                    "provider": "hermes", "status": status,
                })
            rows = [
                ("t-ok", "rt1", "completed", None, None, DAY),
                ("t-true", "rt1", "failed", "process", None, DAY),
                ("t-cancel-kind", "rt1", "failed", "cancelled", None, DAY),
                ("t-cancel-status", "rt1", "cancelled", None, None, DAY),
                ("t-run", "rt1", "running", None, None, DAY),
                ("t-quota", "rt1", "failed", "unknown", "agent_error.provider_quota_limit", DAY),
                ("t-rate", "rt3", "failed", "rate_limit", "provider_capacity", DAY),
                ("t-old", "rt1", "failed", "process", None, "2026-09-01T00:00:00Z"),
            ]
            for tid, rid, status, kind, reason, when in rows:
                store.upsert(conn, "tasks", {
                    "server": SERVER,
                    "workspace_id": "ws-a",
                    "id": tid,
                    "runtime_id": rid,
                    "status": status,
                    "error_kind": kind,
                    "failure_reason": reason,
                    "created_at": when,
                    "started_at": when,
                    "completed_at": None if status == "running" else when,
                })


class ApiShapeTests(unittest.TestCase):
    def test_live_views_age_samples_without_rewriting_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "board.sqlite")
            snapshot = Path(tmp) / "usage-snapshot.json"
            write_snapshot(snapshot, {
                "usage_collected_at": "2026-10-16T00:00:00Z",
                "days": 14,
                "external_quotas": [{
                    "provider": provider,
                    "status": status,
                    "last_success_at": "2026-10-16T16:43:00Z",
                    "windows": [{
                        "key": "primary",
                        "label": "Weekly",
                        "window_minutes": 10080,
                        "used_percent": used,
                        "resets_at": "2026-10-19T16:43:00Z",
                    }],
                } for provider, status, used in (
                    ("cursor", "ok", 10), ("grok", "error", 95),
                )],
            })
            original = snapshot.read_bytes()
            view = Views(store, SERVER, app_url="https://app.test", collect_interval_s=60)
            try:
                for day, alerts in (
                    (16, ["grok:primary"]),
                    (18, ["cursor:primary", "grok:primary"]),
                    (19, ["cursor:primary", "grok:primary"]),
                    (20, ["cursor:primary", "grok:primary"]),
                ):
                    now = NOW.replace(day=day)
                    with self.subTest(now=now), patch("multica_board.views._now", return_value=now):
                        body = view.usage()
                        self.assertEqual(body["snapshot_state"], "partial")
                        self.assertEqual(body["external_quotas"][0]["windows"][0]["signal"], "normal")
                        self.assertEqual(
                            [item["provider"] for item in body["signals"]["dispatchable"]],
                            [],
                        )
                        self.assertEqual(
                            [item["provider"] for item in body["signals"]["exhausting"]],
                            ["grok"] if day == 16 else [],
                        )
                        self.assertEqual(
                            [item["identifier"] for item in view.attention()["quota_alerts"]], alerts,
                        )
                        self.assertEqual(snapshot.read_bytes(), original)
            finally:
                store.close()

    def test_missing_snapshot(self):
        store = Store(":memory:")
        body = Views(store, SERVER, app_url="https://app.test", collect_interval_s=60).usage()
        store.close()
        self.assertEqual(body["snapshot_state"], "missing")
        self.assertIsNone(body["usage_collected_at"])
        self.assertEqual([item["provider"] for item in body["external_quotas"]], ["9router", "orca"])
        self.assertEqual([item["label"] for item in body["external_quotas"]], ["未接入", "未接入"])
        self.assertEqual(body["signals"], {"dispatchable": [], "exhausting": []})
        self.assertEqual(body["multica_daily"]["by_provider"], [])

    def test_attention_text_stays_within_budget(self):
        alerts = [
            {
                "identifier": f"cursor:{i}",
                "title": "额度 " * 30,
                "status": "quota_exhausting",
                "time": "2026-10-16T00:00:00Z",
                "agent_name": None,
            }
            for i in range(80)
        ]
        text = render_attention_text({
            "generated_at": "2026-10-16T00:00:00Z",
            "window_h": 24,
            "workspaces": [],
            "quota_alerts": alerts,
        })
        self.assertLessEqual(len(text.encode()), ATTENTION_TEXT_BYTES)
        self.assertTrue(text.endswith("\n"))
        self.assertIn("…", text)

    def test_response_builder_does_not_sum_units(self):
        body = build_usage_response(
            {
                "usage_collected_at": "2026-10-16T00:00:00Z",
                "days": 14,
                "external_quotas": [{
                    "provider": "cursor",
                    "status": "ok",
                    "windows": [{
                        "key": "primary",
                        "label": "Total",
                        "window_minutes": 43200,
                        "used_percent": 36.43,
                        "resets_at": "2026-10-19T11:37:19Z",
                        "pace": None,
                    }],
                }],
                "token_by_runtime": [],
            },
            [],
            [],
            [],
            now=NOW,
        )
        self.assertEqual(body["external_quotas"][0]["windows"][0]["used_percent"], 36.43)
        self.assertEqual(body["multica_daily"]["by_provider"], [])
        self.assertNotIn("combined", json.dumps(body))


class QuotaPoolTests(unittest.TestCase):
    """whitelist view of the codex_accounts snapshot block (ACM-650)."""

    @staticmethod
    def _account(alias, **overrides):
        record = {
            "alias": alias,
            "plan": "plus",
            "is_system_account": False,
            "account_key": "deadbeef",
            "data_confidence": "exact",
            "windows": [
                {"key": "primary", "label": "5h", "window_minutes": 300, "used_percent": 10, "resets_at": "2026-10-16T21:00:00Z", "reset_description": "soon"},
                {"key": "secondary", "label": "weekly", "window_minutes": 10080, "used_percent": 20, "resets_at": "2026-10-20T16:43:00Z", "reset_description": "later"},
            ],
            "credits_remaining": 100.0,
            "reset_cards": {
                "available_count": 1,
                "soonest_expires_at": "2026-10-17T16:43:00Z",
                "cards": [{"title": f"card {EMAIL}", "token": SECRET}],
            },
            "source_updated_at": "2026-10-16T16:30:00Z",
            "sampled_at": "2026-10-16T16:42:00Z",
            "source": "codexbar-disk",
            "source_path": "CodexBar/codex-account-snapshots.json",
            "account_email": EMAIL,
            "token": SECRET,
        }
        record.update(overrides)
        return record

    @classmethod
    def _snapshot(cls):
        return {
            "usage_collected_at": "2026-10-16T16:43:00Z",
            "days": 14,
            "external_quotas": [],
            "token_by_runtime": [],
            "codex_accounts": {
                "status": "ok",
                "source": "codexbar-disk",
                "sampled_at": "2026-10-16T16:42:30Z",
                "file_mtime": "2026-10-16T16:42:00Z",
                "account_count": 5,
                "error": None,
                "accounts": [
                    # 6e: free plan, monthly window only, excluded from pace by alias.
                    cls._account(
                        "6e",
                        plan="free",
                        windows=[{"key": "primary", "label": "monthly", "window_minutes": 43200, "used_percent": 0.0, "resets_at": "2026-11-15T16:43:00Z", "reset_description": "monthly"}],
                        credits_remaining=62500.0,
                        reset_cards={"available_count": 2, "soonest_expires_at": "2026-10-18T19:14:20Z", "cards": []},
                        source_updated_at="2026-10-16T16:40:00Z",
                    ),
                    # aq: fresh, weekly pace example, no credits, card expiring in ~20h -> flag 24.
                    cls._account(
                        "aq",
                        plan="team",
                        windows=[
                            {"key": "primary", "label": "5h", "window_minutes": 300, "used_percent": 21, "resets_at": "2026-10-16T21:52:09Z"},
                            {"key": "secondary", "label": "weekly", "window_minutes": 10080, "used_percent": 6, "resets_at": "2026-10-20T16:41:00Z"},
                        ],
                        credits_remaining=None,
                        reset_cards={"available_count": 2, "soonest_expires_at": "2026-10-17T12:38:50Z", "cards": []},
                        source_updated_at="2026-10-16T16:41:00Z",
                    ),
                    # he: system account flag is ignored for pace, true zero credits, card in ~6h -> flag 6.
                    cls._account(
                        "he",
                        is_system_account=True,
                        windows=[
                            {"key": "primary", "label": "5h", "window_minutes": 300, "used_percent": 1, "resets_at": "2026-10-16T21:14:48Z"},
                            {"key": "secondary", "label": "weekly", "window_minutes": 10080, "used_percent": 43, "resets_at": "2026-10-22T16:42:30Z"},
                        ],
                        credits_remaining=0.0,
                        reset_cards={"available_count": 2, "soonest_expires_at": "2026-10-16T22:42:36Z", "cards": []},
                        source_updated_at="2026-10-16T16:42:30Z",
                    ),
                    # mg: stale (>1800s), zero cards, resets past flag horizon -> flag null.
                    cls._account(
                        "mg",
                        windows=[
                            {"key": "primary", "label": "5h", "window_minutes": 300, "used_percent": 11, "resets_at": "2026-10-17T01:01:21Z"},
                            {"key": "secondary", "label": "weekly", "window_minutes": 10080, "used_percent": 93, "resets_at": "2026-10-21T00:40:00Z"},
                        ],
                        credits_remaining=72807.3522155,
                        reset_cards={"available_count": 0, "soonest_expires_at": None, "cards": []},
                        source_updated_at="2026-10-16T00:40:00Z",
                    ),
                    # pe: exactly 1800s old -> still fresh; weekly just started -> pace null; no credits.
                    cls._account(
                        "pe",
                        plan="team",
                        windows=[
                            {"key": "primary", "label": "5h", "window_minutes": 300, "used_percent": 1, "resets_at": "2026-10-16T21:16:58Z"},
                            {"key": "secondary", "label": "weekly", "window_minutes": 10080, "used_percent": 6, "resets_at": "2026-10-23T16:30:00Z"},
                        ],
                        credits_remaining=None,
                        reset_cards={"available_count": 0, "soonest_expires_at": None, "cards": []},
                        source_updated_at="2026-10-16T16:13:00Z",
                    ),
                ],
            },
        }

    def _body(self, snapshot=None):
        return build_usage_response(self._snapshot() if snapshot is None else snapshot, [], [], [], now=NOW)

    def test_structure_and_fixed_order(self):
        pool = self._body()["quota_pool"]
        self.assertEqual(pool["status"], "ok")
        self.assertEqual(pool["source"], "codexbar-disk")
        self.assertEqual(pool["sampled_at"], "2026-10-16T16:42:30Z")
        self.assertEqual(pool["stale_after_s"], 1800)
        self.assertEqual(pool["account_count"], 5)
        self.assertEqual(pool["present_aliases"], ["6e", "aq", "he", "mg", "pe"])
        self.assertEqual([item["alias"] for item in pool["accounts"]], ["6e", "aq", "he", "mg", "pe"])
        for account in pool["accounts"]:
            self.assertEqual(
                set(account.keys()),
                {"alias", "present", "plan", "pace_applicable", "windows", "reset_cards",
                 "credits", "source_updated_at", "sample_age_s", "stale", "pace"},
            )

    def test_per_account_fields(self):
        accounts = {item["alias"]: item for item in self._body()["quota_pool"]["accounts"]}
        free = accounts["6e"]
        self.assertEqual(free["plan"], "free")
        self.assertEqual(free["windows"], {"5h": None, "weekly": None})
        self.assertFalse(free["pace_applicable"])
        self.assertEqual(free["pace"], {"ratio": None, "reference_at": None})
        self.assertEqual(free["credits"], {"present": True, "remaining": 62500.0, "unit": "Credits"})
        self.assertEqual(free["reset_cards"]["expiry_flag"], 72)

        he = accounts["he"]
        self.assertEqual(he["credits"], {"present": True, "remaining": 0.0, "unit": "Credits"})
        self.assertTrue(he["pace_applicable"])
        self.assertEqual(he["reset_cards"]["expiry_flag"], 6)
        self.assertEqual(he["pace"]["ratio"], 3.01)
        self.assertEqual(he["pace"]["reference_at"], "2026-10-16T16:42:30Z")

        aq = accounts["aq"]
        self.assertEqual(aq["credits"], {"present": False, "remaining": None, "unit": "Credits"})
        self.assertEqual(aq["windows"]["5h"]["used_percent"], 21)
        self.assertEqual(aq["windows"]["weekly"]["window_minutes"], 10080)
        self.assertEqual(aq["windows"]["5h"]["hours_until_reset"], 5.2)
        self.assertEqual(aq["reset_cards"]["expiry_flag"], 24)
        self.assertEqual(aq["pace"]["ratio"], 0.14)
        self.assertEqual(aq["pace"]["reference_at"], "2026-10-16T16:41:00Z")

        mg = accounts["mg"]
        self.assertTrue(mg["stale"])
        self.assertEqual(mg["sample_age_s"], 57780)
        self.assertIsNone(mg["reset_cards"]["expiry_flag"])
        self.assertEqual(mg["reset_cards"]["available_count"], 0)
        self.assertEqual(mg["pace"]["ratio"], 3.26)

        pe = accounts["pe"]
        self.assertFalse(pe["stale"])
        self.assertEqual(pe["sample_age_s"], 1800)
        self.assertIsNone(pe["pace"]["ratio"])
        self.assertEqual(pe["pace"]["reference_at"], "2026-10-16T16:13:00Z")
        self.assertEqual(pe["credits"]["present"], False)

    def test_stale_boundary_and_missing_timestamp(self):
        snapshot = self._snapshot()
        records = snapshot["codex_accounts"]["accounts"]
        records[4]["source_updated_at"] = "2026-10-16T16:12:59Z"  # 1801s -> stale
        accounts = {a["alias"]: a for a in self._body(snapshot)["quota_pool"]["accounts"]}
        self.assertTrue(accounts["pe"]["stale"])
        records[4]["source_updated_at"] = "2026-10-16T16:13:00Z"  # exactly 1800s -> fresh
        accounts = {a["alias"]: a for a in self._body(snapshot)["quota_pool"]["accounts"]}
        self.assertFalse(accounts["pe"]["stale"])
        records[4]["source_updated_at"] = None
        accounts = {a["alias"]: a for a in self._body(snapshot)["quota_pool"]["accounts"]}
        self.assertIsNone(accounts["pe"]["sample_age_s"])
        self.assertTrue(accounts["pe"]["stale"])

    def test_expiry_flag_boundaries(self):
        snapshot = self._snapshot()
        cards = snapshot["codex_accounts"]["accounts"][0]["reset_cards"]
        for soonest, expected in (
            ("2026-10-19T16:43:00Z", 72),   # exactly 72h
            ("2026-10-19T16:49:00Z", None), # 72.1h
            ("2026-10-17T16:43:00Z", 24),   # exactly 24h
            ("2026-10-16T22:43:00Z", 6),    # exactly 6h
            ("2026-10-16T16:30:00Z", 6),    # already past, still the smallest bucket
        ):
            with self.subTest(soonest=soonest):
                cards["soonest_expires_at"] = soonest
                flags = {a["alias"]: a["reset_cards"]["expiry_flag"] for a in self._body(snapshot)["quota_pool"]["accounts"]}
                self.assertEqual(flags["6e"], expected)
        cards["available_count"] = 0
        cards["soonest_expires_at"] = "2026-10-16T22:43:00Z"
        flags = {a["alias"]: a["reset_cards"]["expiry_flag"] for a in self._body(snapshot)["quota_pool"]["accounts"]}
        self.assertIsNone(flags["6e"])

    def test_hours_until_soonest_can_be_negative(self):
        snapshot = self._snapshot()
        cards = snapshot["codex_accounts"]["accounts"][0]["reset_cards"]
        cards["soonest_expires_at"] = "2026-10-16T15:43:00Z"  # 1h ago
        account = self._body(snapshot)["quota_pool"]["accounts"][0]
        self.assertEqual(account["reset_cards"]["hours_until_soonest"], -1.0)

    def test_missing_block_and_bad_status(self):
        for snapshot, status in (
            ({"usage_collected_at": "2026-10-16T16:43:00Z", "days": 14}, "missing"),
            ({"codex_accounts": "not-a-dict"}, "missing"),
            ({"codex_accounts": {"status": "weird", "accounts": []}}, "error"),
            ({"codex_accounts": {"status": "empty", "accounts": []}}, "empty"),
        ):
            with self.subTest(status=status):
                pool = self._body(snapshot)["quota_pool"]
                self.assertEqual(pool["status"], status)
                self.assertEqual(pool["account_count"], 0)
                self.assertEqual(len(pool["accounts"]), 5)
                self.assertTrue(all(account["present"] is False for account in pool["accounts"]))
                self.assertTrue(all(account["sample_age_s"] is None for account in pool["accounts"]))

    def test_whitelist_drops_identity_and_card_details(self):
        body = self._body()
        text = json.dumps(body, ensure_ascii=False)
        for leaked in ("account_key", "source_path", "@", SECRET, EMAIL, "reset_description", "data_confidence", "is_system_account", "deadbeef"):
            self.assertNotIn(leaked, text)
        cards = body["quota_pool"]["accounts"][0]["reset_cards"]
        self.assertNotIn("cards", cards)


class CliTests(unittest.TestCase):
    def test_collect_usage_command(self):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "board.sqlite"
            multica = root / "multica"
            codexbar = root / "codexbar"
            multica.write_text(
                "#!/usr/bin/env python3\n"
                "import json, sys\n"
                "args = sys.argv[1:]\n"
                "if 'workspace' in args and 'list' in args:\n"
                "    data = [{'id': 'ws-a', 'slug': 'alpha', 'name': 'Alpha'}]\n"
                "elif 'usage' in args:\n"
                f"    data = [{{'workspace_id': 'ws-a', 'runtime_id': 'rt1', 'date': '{today}', 'provider': 'hermes', 'input_tokens': 3, 'output_tokens': 1, 'cache_read_tokens': 0, 'cache_write_tokens': 0, 'cost_usd_ticks': 0}}]\n"
                "elif 'activity' in args:\n"
                "    data = [{'hour': 2, 'count': 1}]\n"
                "else:\n"
                "    sys.stderr.write('unexpected')\n"
                "    sys.exit(1)\n"
                "json.dump(data, sys.stdout)\n",
                encoding="utf-8",
            )
            codexbar.write_text(
                "#!/usr/bin/env python3\n"
                "import json, sys\n"
                "provider = sys.argv[sys.argv.index('--provider') + 1]\n"
                "json.dump([{'source': 'web', 'rateWindowLabels': {'primary': 'Weekly'}, 'usage': {'accountEmail': '%s', 'primary': {'usedPercent': 10, 'windowMinutes': 10080, 'resetsAt': '2026-12-01T00:00:00Z'}, 'updatedAt': '2026-10-16T00:00:00Z'}}], sys.stdout)\n" % EMAIL,
                encoding="utf-8",
            )
            os.chmod(multica, 0o755)
            os.chmod(codexbar, 0o755)
            store = Store(db)
            with store.tx() as conn:
                store.upsert(conn, "workspaces", {"server": DEFAULT_PROFILE, "id": "ws-a", "name": "Alpha", "slug": "alpha"})
                store.upsert(conn, "runtimes", {
                    "server": DEFAULT_PROFILE, "workspace_id": "ws-a", "id": "rt1",
                    "name": "Hermes", "provider": "hermes", "status": "online",
                })
            store.close()
            code = main([
                "collect-usage", "--all-workspaces",
                "--db", str(db),
                "--multica-bin", str(multica),
                "--codexbar-bin", str(codexbar),
                "--profile", DEFAULT_PROFILE,
            ])
            self.assertEqual(code, 0)
            text = (root / "usage-snapshot.json").read_text(encoding="utf-8")
            self.assertNotIn(EMAIL, text)
            self.assertIn("未接入", text)
            self.assertEqual(_mode(root / "usage-snapshot.json"), 0o600)
            saved = json.loads(text)
            daily = saved["token_by_runtime"][0]["daily"][0]
            self.assertEqual(daily["date"], today)
            self.assertEqual(daily["input_tokens"], 3)
            self.assertTrue((root / "usage-history.jsonl").is_file())

    def test_launchd_template_is_not_installed(self):
        """The checked-in file is a template. An operator install is outside this test."""
        path = Path(__file__).resolve().parents[2] / "launchd" / "one.yic.multica-usage-collector.plist"
        text = path.read_text(encoding="utf-8")
        self.assertEqual(path.parent.name, "launchd")
        self.assertIn("one.yic.multica-usage-collector", text)
        self.assertIn("<integer>900</integer>", text)
        self.assertIn("Do not copy", text)


if __name__ == "__main__":
    unittest.main()
