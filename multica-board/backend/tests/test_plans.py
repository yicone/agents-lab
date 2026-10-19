from __future__ import annotations

import base64
import copy
import hashlib
import http.client
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from multica_board.cli_runner import CliError, CliResult, CliRunner
from multica_board.plan_contract import canonical, decide, envelope, strict_json, validate_payload
from multica_board.plans import PlanError, Plans, write_plan
from multica_board.runtime_plan import build_plan, main, operation, read_live, selection
from multica_board.server import BoardServer
from multica_board.store import Store
from multica_board.views import Views

WS = "22222222-2222-4222-8222-222222222222"
WS2 = "22222222-2222-4222-8222-222222222223"
AG = "33333333-3333-4333-8333-333333333333"
RT = "44444444-4444-4444-8444-444444444444"
USER = "11111111-1111-4111-8111-111111111111"
PID = "55555555-5555-4555-8555-555555555555"
DAY = "2026-10-19T04:00:00Z"
NOW = datetime(2026, 10, 19, 4, tzinfo=timezone.utc)
ORIGIN = "https://api.example.test"


def snapshot():
    return {"server_identity": ORIGIN, "observed_at": DAY,
            "workspaces": [{"id": WS, "name": "测试"}],
            "agents": [{"workspace_id": WS, "id": AG, "name": "Builder", "archived": False,
                        "runtime_id": RT, "model": "", "thinking_level": None, "service_tier": "priority"}],
            "runtimes": [{"workspace_id": WS, "id": RT, "name": "Target runtime", "provider": "codex", "status": "online", "last_seen_at": DAY}],
            "tasks": [{"workspace_id": WS, "agent_id": AG, "pages_read": 1, "runs": []}]}


def make(s=None, **kw):
    options = dict(origin=ORIGIN, actor={"type": "member", "id": USER, "verified": False},
                   on_behalf_of=USER, workspaces=[WS], selected=[(WS, AG)], target=RT,
                   operations={"model": "omit", "thinking_level": "omit", "service_tier": "clear"},
                   now=NOW, plan_id=PID)
    options.update(kw)
    return build_plan(s or snapshot(), **options)


class ContractTests(unittest.TestCase):
    def test_null_empty_omit_clear_and_missing_are_distinct(self):
        p = make()["payload"]
        f = p["rows"][0]["fields"]
        self.assertEqual(f["model"], {"op": "omit", "old": "", "new": "", "old_known": True})
        self.assertIsNone(f["thinking_level"]["old"])
        self.assertEqual(f["service_tier"]["op"], "clear")
        self.assertIsNone(f["service_tier"]["new"])
        s = snapshot()
        del s["agents"][0]["model"]
        unknown = make(s)["payload"]["rows"][0]
        self.assertFalse(unknown["fields"]["model"]["old_known"])
        self.assertIn("BLOCKED_SOURCE", unknown["reasons"])
        changed = snapshot()
        changed["agents"][0]["model"] = None
        self.assertNotEqual(make()["plan_hash"], make(changed)["plan_hash"])

    def test_active_all_four_states_and_dedup(self):
        for state in ("queued", "dispatched", "running", "waiting_local_directory"):
            s = snapshot()
            run = {"id": PID, "status": state, "created_at": DAY, "result": "SECRET"}
            s["tasks"][0]["runs"] = [run, run]
            p = make(s)
            row = p["payload"]["rows"][0]
            self.assertEqual(row["decision"], "SKIP_ACTIVE")
            self.assertEqual(row["activity"]["tasks_seen"], 1)
            self.assertNotIn("SECRET", json.dumps(p))
            self.assertEqual(sum(p["payload"]["counts"].values()), 1)

    def test_unknown_and_short_page_never_prove_idle(self):
        for state in ("completed", "new_server_state"):
            s = snapshot()
            s["tasks"][0]["runs"] = [{"id": PID, "status": state, "created_at": DAY}]
            row = make(s, snapshot_kind="live_cli")["payload"]["rows"][0]
            self.assertEqual(row["decision"], "BLOCKED")
            self.assertEqual(row["activity"]["completeness"], "UNKNOWN")
            self.assertIn("BLOCKED_ACTIVITY_COMPLETENESS", row["reasons"])

    def test_scope_and_operations_rejected(self):
        for ws, ag in (([WS], [f"{WS2}:{AG}"]), ([WS, WS], [f"{WS}:{AG}"]), ([WS], [f"{WS}:{AG}"] * 2)):
            with self.assertRaises(ValueError):
                selection(ws, ag)
        for op in ("set:", "set: x", "set:x ", "default", "set:x\n"):
            with self.assertRaises(ValueError):
                operation(op)
        for ttl in (0, 601):
            with self.assertRaises(ValueError):
                make(ttl=ttl)

    def test_schema_one_rejects_zero_or_multiple_workspaces(self):
        for scopes in ([], [WS, WS2], [WS, WS]):
            with self.subTest(scopes=scopes):
                with self.assertRaises(ValueError):
                    selection(scopes, [f"{WS}:{AG}"])
                payload = make()["payload"]
                payload["workspace_allow_list"] = [{"id": w, "name": None} for w in scopes]
                with self.assertRaisesRegex(ValueError, "exactly one workspace"):
                    validate_payload(payload)
                with self.assertRaises(ValueError):
                    make(workspaces=scopes)

    def test_repeated_workspace_cli_rejected_before_reads_or_files(self):
        for extra in (WS, WS2):
            with tempfile.TemporaryDirectory() as temp:
                output = Path(temp) / "no-plan"
                with patch("multica_board.runtime_plan.CliRunner") as runner:
                    result = main(["--server", ORIGIN, "--actor", f"member:{USER}",
                                   "--on-behalf-of", USER, "--workspace-allow", WS,
                                   "--workspace-allow", extra, "--agent", f"{WS}:{AG}",
                                   "--target-runtime", RT, "--plan-dir", str(output)])
                self.assertEqual(result, 2)
                runner.assert_not_called()
                self.assertFalse(output.exists())

    def test_runtime_metadata_survives_snapshot(self):
        row = make()["payload"]["rows"][0]
        expected = {k: snapshot()["runtimes"][0][k] for k in ("id", "name", "provider", "status", "last_seen_at")}
        self.assertEqual(row["source_runtime"], expected)
        self.assertEqual(row["target_runtime"], expected)

    def test_value_change_is_not_implied_by_set_or_clear(self):
        row = make(operations={"model": "omit", "thinking_level": "clear", "service_tier": "omit"})["payload"]["rows"][0]
        self.assertEqual(row["fields"]["runtime_id"]["op"], "set")
        self.assertEqual(row["fields"]["thinking_level"]["op"], "clear")
        self.assertEqual(row["decision"], "BLOCKED")
        for check in row["checks"].values():
            check["status"] = "PASS"
        self.assertEqual(decide(row, "live_cli")[0], "NO_CHANGE")
        row["fields"]["model"]["new"] = None
        self.assertEqual(decide(row, "live_cli")[0], "APPROVABLE")
        row["fields"]["model"]["old_known"] = False
        self.assertEqual(decide(row, "live_cli")[0], "BLOCKED")

    def test_meta_origin_resolves_cli_profile_without_pinning_collector(self):
        execute = Mock(return_value=subprocess.CompletedProcess([], 0,
            "Server:  https://API.Example.Test:443/\nUser: PRIVATE_IDENTITY\n", ""))
        runner = CliRunner(profile="custom-profile", _exec=execute, retries=0)
        origin = runner.run(["auth", "status"]).data
        self.assertEqual(origin, {"server_identity": ORIGIN})
        self.assertNotIn("PRIVATE_IDENTITY", json.dumps(origin))
        self.assertNotIn("--output", execute.call_args.args[0])
        execute.return_value = subprocess.CompletedProcess(
            [], 0, "", "Server:  https://API.Example.Test:443/\nUser: PRIVATE_IDENTITY\nToken: secret\n",
        )
        origin = runner.run(["auth", "status"]).data
        self.assertEqual(origin, {"server_identity": ORIGIN})
        self.assertNotIn("PRIVATE_IDENTITY", json.dumps(origin))
        self.assertNotIn("secret", json.dumps(origin))
        execute.return_value = subprocess.CompletedProcess(
            [], 0, "Server: https://a.test\n", "Server: https://b.test\n",
        )
        with self.assertRaises(CliError):
            runner.run(["auth", "status"])
        execute.return_value = subprocess.CompletedProcess(
            [], 0, "Server:  https://API.Example.Test:443/\nUser: PRIVATE_IDENTITY\n", "",
        )
        a = Mock()
        a.profile, a.app_url, a.refresh = "custom-profile", "https://web.example.test", 0
        a.static_dir, a.host, a.port = None, "127.0.0.1", 0
        captured = {}
        class Server:
            server_address = ("127.0.0.1", 0)
            def __init__(self, host, port, views, **kwargs):
                captured["meta"] = views.meta()
            def serve_forever(self):
                pass
            def server_close(self):
                pass
        with tempfile.TemporaryDirectory() as temp:
            a.db, a.plan_dir = Path(temp) / "board.sqlite", Path(temp) / "plans"
            from multica_board.__main__ import cmd_serve
            with patch("multica_board.__main__._runner", return_value=runner), patch("multica_board.__main__.BoardServer", Server):
                self.assertEqual(cmd_serve(a), 0)
        self.assertEqual(captured["meta"]["server_identity"], ORIGIN)
        self.assertIsNone(runner.server_url)
        execute.return_value = subprocess.CompletedProcess([], 0, "[]", "")
        runner.run(["workspace", "list"])
        argv = execute.call_args.args[0]
        self.assertNotIn("--server-url", argv)

    def test_auth_failure_still_serves_http_meta_with_null_origin(self):
        from multica_board.__main__ import cmd_serve
        captured = {}
        class Server(BoardServer):
            def serve_forever(self):
                thread = threading.Thread(target=super().serve_forever)
                thread.start()
                try:
                    c = http.client.HTTPConnection(*self.server_address)
                    c.request("GET", "/api/meta")
                    response = c.getresponse()
                    captured["status"] = response.status
                    captured["body"] = json.loads(response.read())
                    c.close()
                finally:
                    self.shutdown()
                    thread.join()
        runner = CliRunner(profile="custom", retries=0, _exec=Mock(
            return_value=subprocess.CompletedProcess([], 1, "", "offline")))
        with tempfile.TemporaryDirectory() as temp:
            a = Mock(profile="custom", app_url="https://web.example.test", refresh=0,
                     static_dir=None, host="127.0.0.1", port=0,
                     db=Path(temp)/"board.sqlite", plan_dir=Path(temp)/"plans")
            with patch("multica_board.__main__._runner", return_value=runner), \
                 patch("multica_board.__main__.BoardServer", Server), \
                 patch("multica_board.__main__.signal.signal"):
                self.assertEqual(cmd_serve(a), 0)
        self.assertEqual(captured["status"], 200)
        self.assertIsNone(captured["body"]["server_identity"])
        self.assertIsNone(runner.server_url)

    def test_archived_unknown_is_not_false(self):
        for agent in (None, {k: v for k, v in snapshot()["agents"][0].items() if k != "archived"}):
            s = snapshot()
            s["agents"] = [] if agent is None else [agent]
            row = make(s)["payload"]["rows"][0]
            self.assertIsNone(row["archived"])
        for value in (True, False, None):
            s = snapshot()
            s["agents"][0]["archived"] = value
            self.assertIs(make(s)["payload"]["rows"][0]["archived"], value)
        s["agents"][0]["archived"] = "false"
        with self.assertRaises(ValueError):
            make(s)

    def test_live_archived_missing_and_read_failure_are_unknown(self):
        for missing_agent in (False, True):
            s = snapshot()
            del s["agents"][0]["archived"]
            def run(args, workspace_id=None):
                if args[:2] == ["workspace", "list"]:
                    return CliResult(s["workspaces"])
                if args[:2] == ["agent", "list"]:
                    if missing_agent:
                        raise CliError("read refused")
                    return CliResult(s["agents"])
                if args[:2] == ["runtime", "list"]:
                    return CliResult(s["runtimes"])
                raise CliError("tasks refused")
            result = read_live(Mock(run=run), ORIGIN, [WS], [(WS, AG)], now=NOW)
            row = make(result, snapshot_kind="live_cli")["payload"]["rows"][0]
            self.assertIsNone(row["archived"])
            self.assertEqual(row["activity"]["pages_read"], 0)
            self.assertEqual(row["decision"], "BLOCKED")

    def test_historical_health_is_unknown_and_enforced(self):
        for status in ("online", "offline"):
            s = snapshot()
            s["runtimes"][0]["status"] = status
            for now in (NOW, NOW + timedelta(hours=1)):
                p = make(s, now=now)["payload"]
                check = p["rows"][0]["checks"]["health"]
                self.assertEqual(check["status"], "UNKNOWN")
                self.assertEqual(check["source"], "historical_snapshot")
                for forged in ("PASS", "FAIL"):
                    check["status"] = forged
                    with self.assertRaises(ValueError):
                        validate_payload(p)
                check.update(status="UNKNOWN", source="runtime_list_status_and_heartbeat")
                with self.assertRaises(ValueError):
                    validate_payload(p)
        live = make(snapshot_kind="live_cli")["payload"]["rows"][0]
        self.assertEqual(live["checks"]["health"]["status"], "PASS")

    def test_unconfirmed_meta_origin_is_not_guessed(self):
        for stdout in ("User: PRIVATE_IDENTITY", "Server: https://a.test\nServer: https://b.test", "Server: https://u:secret@a.test"):
            runner = CliRunner(profile="custom", retries=0, _exec=Mock(return_value=subprocess.CompletedProcess([], 0, stdout, "")))
            with self.assertRaisesRegex(CliError, "identity could not be verified"):
                runner.run(["auth", "status"])

    def test_target_workspace_mismatch_and_historical(self):
        s = snapshot()
        s["runtimes"][0]["workspace_id"] = WS2
        row = make(s)["payload"]["rows"][0]
        self.assertIn("BLOCKED_TARGET", row["reasons"])
        self.assertIn("BLOCKED_HISTORICAL", row["reasons"])
        self.assertEqual(row["checks"]["health"]["status"], "UNKNOWN")

    def test_jcs_utf16_order_and_rejected_domain(self):
        self.assertEqual(canonical({"z": None, "a": "\n\"\\", "b": True}), b'{"a":"\\n\\\"\\\\","b":true,"z":null}')
        self.assertEqual(canonical({"\ue000": 1, "\U00010000": 2}), '{"𐀀":2,"\ue000":1}'.encode())
        for v in (1.0, float("nan"), 2**53, "\ud800"):
            with self.assertRaises((ValueError, UnicodeError)):
                canonical(v)
        for raw in (b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":1.1}'):
            with self.assertRaises(ValueError):
                strict_json(raw)

    def test_forged_unknown_fields_scope_count_and_evidence_rejected(self):
        for mutate in (
            lambda p: p.update(secret="TOKEN"),
            lambda p: p.update(mode="apply_candidate"),
            lambda p: p.update(schema_version=2),
            lambda p: p["rows"][0]["checks"]["permission"].update(status="PASS"),
            lambda p: p["rows"][0].update(agent_id=USER),
            lambda p: p["counts"].update(APPROVABLE=1),
            lambda p: p["rows"][0]["activity"].update(completeness="PASS"),
        ):
            p = make()["payload"]
            mutate(p)
            with self.assertRaises(ValueError):
                validate_payload(p)

    def test_live_uses_only_scoped_reads_and_whitelists(self):
        class Runner:
            calls = []
            def run(self, args, workspace_id=None):
                self.calls.append((args, workspace_id))
                s = snapshot()
                data = {("workspace", "list"): s["workspaces"], ("agent", "list"): s["agents"],
                        ("runtime", "list"): s["runtimes"], ("agent", "tasks"): []}[tuple(args[:2])]
                data = copy.deepcopy(data)
                for r in data:
                    r["custom_env"] = "SECRET"
                return CliResult(data)
        r = Runner()
        s = read_live(r, ORIGIN, [WS], [(WS, AG)], now=NOW)
        self.assertEqual(len(r.calls), 4)
        self.assertTrue(all(ws == WS for _, ws in r.calls[1:]))
        self.assertNotIn("SECRET", json.dumps(s))
        self.assertEqual(make(s, snapshot_kind="live_cli")["payload"]["counts"]["APPROVABLE"], 0)


class FileAndHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name).resolve() / "plans"
        self.plan = make()
        write_plan(self.directory, self.plan)
        self.file = self.directory / f"{PID}.json"
        self.plans = Plans(self.directory)

    def test_roundtrip_permissions_canonical_hash_and_g2(self):
        self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.file.stat().st_mode), 0o600)
        detail = self.plans.detail(PID)
        self.assertEqual(detail["plan"], self.plan)
        item = self.plans.list()["items"][0]
        self.assertEqual(item["snapshot_kind"], "historical")
        self.assertEqual(item["observed_at"], DAY)
        self.assertTrue(detail["hash_verified"])
        raw = base64.b64decode(detail["canonical_payload_b64"])
        self.assertEqual(hashlib.sha256(raw).hexdigest(), self.plan["plan_hash"])
        self.assertEqual(set(detail["gates"]["g2"].values()), {"absent"})
        self.assertFalse(detail["apply_enabled"])
        self.assertEqual(detail["plan"]["payload"]["rows"][0]["fields"]["model"]["old"], "")
        with patch("multica_board.plans.datetime") as clock:
            clock.now.return_value = NOW + timedelta(seconds=600)
            self.assertTrue(self.plans.detail(PID)["expired"])
        with self.assertRaises(FileExistsError):
            write_plan(self.directory, self.plan)

    def test_hash_mismatch_is_explicit(self):
        p = copy.deepcopy(self.plan)
        p["payload"]["selection_reason"] = "Changed selection justification"
        self.file.write_bytes(canonical(p))
        detail = self.plans.detail(PID)
        self.assertFalse(detail["hash_verified"])
        self.assertEqual(base64.b64decode(detail["canonical_payload_b64"]), canonical(p["payload"]))
        self.assertFalse(self.plans.list()["items"][0]["hash_verified"])

    def test_errors_remain_in_list_without_leaking_raw(self):
        cases = [(b'{"token":"SECRET"', "invalid_json"),
                 (b'{"payload":{},"hash_algo":"x","plan_hash":"x"}', "invalid_schema"),
                 (b" " * (2 * 1024 * 1024 + 1), "too_large")]
        for raw, code in cases:
            self.file.write_bytes(raw)
            listing = self.plans.list()
            self.assertEqual(listing["items"][0]["parse_error"], code)
            self.assertNotIn("SECRET", json.dumps(listing))
            with self.assertRaisesRegex(PlanError, code):
                self.plans.detail(PID)

    def test_bad_modes_symlink_hardlink_and_directory(self):
        self.file.chmod(0o644)
        self.assertEqual(self.plans.list()["items"][0]["parse_error"], "permissions")
        self.file.chmod(0o600)
        linked = self.directory / f"{USER}.json"
        linked.symlink_to(self.file)
        self.assertEqual(next(i for i in self.plans.list()["items"] if i["plan_id"] == USER)["parse_error"], "permissions")
        linked.unlink()
        os.link(self.file, linked)
        self.assertTrue(all(i["parse_error"] == "permissions" for i in self.plans.list()["items"]))
        self.directory.chmod(0o755)
        self.assertFalse(self.plans.list()["complete"])
        self.assertEqual(self.plans.list()["directory_error"], "permissions")

    def test_get_missing_directory_does_not_create(self):
        absent = self.directory / "missing"
        self.assertEqual(Plans(absent).list()["items"], [])
        self.assertFalse(absent.exists())

    def test_directory_limit_selects_uuid_order_not_latest(self):
        names = [f"00000000-0000-4000-8000-{i:012x}.json" for i in range(1001)]
        def read(fd, name):
            plan = copy.deepcopy(self.plan)
            plan["payload"]["plan_id"] = name[:-5]
            if name == names[-1]:
                plan["payload"]["generated_at"] = "2026-10-20T00:00:00Z"
            return plan, b"", True
        with patch("multica_board.plans.os.listdir", return_value=list(reversed(names))), \
             patch("multica_board.plans._read", side_effect=read):
            response = self.plans.list()
        self.assertFalse(response["complete"])
        self.assertEqual(response["directory_error"], "directory_limit")
        self.assertEqual(len(response["items"]), 1000)
        ids = {i["plan_id"] for i in response["items"]}
        self.assertIn(names[0][:-5], ids)
        self.assertNotIn(names[-1][:-5], ids)

    def test_parent_symlink_is_refused_before_creation(self):
        link = self.directory / "link"
        target = Path(self.temp.name).resolve() / "target"
        target.mkdir(mode=0o700)
        link.symlink_to(target)
        with self.assertRaises(OSError):
            write_plan(link / "must-not-be-created", self.plan)
        self.assertFalse((target / "must-not-be-created").exists())

    def test_task_environment_cannot_fallback_to_host_login(self):
        with patch.dict(os.environ, {"MULTICA_TASK_ID": PID}), patch("multica_board.runtime_plan.CliRunner") as runner:
            result = main(["--server", ORIGIN, "--actor", f"agent:{AG}", "--on-behalf-of", USER,
                           "--workspace-allow", WS, "--agent", f"{WS}:{AG}", "--target-runtime", RT])
        self.assertEqual(result, 2)
        runner.assert_not_called()

    def test_http_security_methods_errors_and_no_file_mutation(self):
        store = Store(Path(self.temp.name) / "board.sqlite")
        self.addCleanup(store.close)
        server = BoardServer("127.0.0.1", 0, Views(store, "test", app_url="https://web.example.test", collect_interval_s=0, server_identity=ORIGIN), plan_dir=self.directory)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        def cleanup():
            server.shutdown()
            thread.join()
            server.server_close()
        self.addCleanup(cleanup)
        def request(method, url, headers=None):
            c = http.client.HTTPConnection(*server.server_address)
            c.request(method, url, headers=headers or {})
            r = c.getresponse()
            result = r.status, r.read(), dict(r.getheaders())
            c.close()
            return result
        before = self.file.read_bytes(), self.file.stat().st_mtime_ns
        status, raw, headers = request("GET", f"/api/plans/{PID}")
        self.assertEqual(status, 200)
        detail = json.loads(raw)
        self.assertTrue(detail["hash_verified"])
        fields = detail["plan"]["payload"]["rows"][0]["fields"]
        self.assertEqual(fields["model"]["old"], "")
        self.assertIsNone(fields["thinking_level"]["old"])
        self.assertEqual(base64.b64decode(detail["canonical_payload_b64"]), canonical(detail["plan"]["payload"]))
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(json.loads(request("GET", "/api/meta")[1])["server_identity"], ORIGIN)
        self.assertEqual(request("GET", "/api/plans")[0], 200)
        self.assertEqual(request("GET", "/api/plans?x=1")[0], 400)
        self.assertEqual(request("GET", "/api/plans/not-a-uuid")[0], 400)
        self.assertEqual(request("GET", f"/api/plans/{USER}")[0], 404)
        self.assertEqual(request("HEAD", f"/api/plans/{PID}")[1], b"")
        for method in ("POST", "PUT", "PATCH", "DELETE", "OPTIONS"):
            self.assertEqual(request(method, "/api/plans")[0], 405)
        self.assertEqual(request("GET", "/api/plans", {"Origin": "https://evil.test"})[0], 403)
        self.assertEqual(request("GET", "/api/plans", {"Host": "evil.test"})[0], 421)
        self.assertEqual(before, (self.file.read_bytes(), self.file.stat().st_mtime_ns))
        altered = copy.deepcopy(self.plan)
        altered["payload"]["selection_reason"] = "Changed without updating hash"
        self.file.write_bytes(canonical(altered))
        status, raw, _ = request("GET", f"/api/plans/{PID}")
        self.assertEqual(status, 200)
        mismatch = json.loads(raw)
        self.assertFalse(mismatch["hash_verified"])
        self.assertEqual(base64.b64decode(mismatch["canonical_payload_b64"]), canonical(altered["payload"]))
        self.file.write_bytes(b"bad json")
        status, raw, _ = request("GET", f"/api/plans/{PID}")
        self.assertEqual(status, 422)
        self.assertEqual(json.loads(raw)["error"]["code"], "plan_unreadable")

    def test_offline_cli_end_to_end(self):
        input_file = Path(self.temp.name) / "snapshot.json"
        input_file.write_text(json.dumps(snapshot()))
        output = Path(self.temp.name).resolve() / "generated"
        with patch("multica_board.runtime_plan.datetime") as clock:
            clock.now.return_value = NOW
            result = main(["--server", ORIGIN, "--actor", f"member:{USER}", "--on-behalf-of", USER,
                           "--workspace-allow", WS, "--agent", f"{WS}:{AG}", "--target-runtime", RT,
                           "--snapshot", str(input_file), "--plan-dir", str(output)])
        self.assertEqual(result, 0)
        self.assertEqual(Plans(output).list()["items"][0]["mode"], "review_only")


if __name__ == "__main__":
    unittest.main()
