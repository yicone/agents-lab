"""Upstream version checks stay read-only and never treat a failed request as current."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from multica_board import skill_upgrade  # noqa: E402
from multica_board.skill_upgrade import check_upgrades, clear_cache, default_fetch  # noqa: E402
from multica_board.skills_view import build_payload  # noqa: E402

OLD = "a" * 40
NEW = "b" * 40
NOW = datetime(2026, 10, 23, 1, 0, tzinfo=timezone.utc)


def tree(path: str, sha: str, *, truncated: bool = False, root: str | None = None) -> dict:
    return {
        "sha": root or ("c" * 40),
        "truncated": truncated,
        "tree": [{"path": path, "type": "tree", "sha": sha}, {"path": path + "/SKILL.md", "type": "blob", "sha": "d" * 40}],
    }


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


class UpgradeTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_cache()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()

    def tearDown(self) -> None:
        clear_cache()
        self.tmp.cleanup()

    def check(self, items, lock=None, fetch=None, **kwargs):
        return check_upgrades(
            items,
            lock or {},
            repo_root=self.repo,
            fetch=fetch or (lambda url: (200, {}, "")),
            mono=kwargs.pop("mono", Clock()),
            sleep=kwargs.pop("sleep", lambda _seconds: None),
            min_interval=kwargs.pop("min_interval", 0),
            **kwargs,
        )

    def test_github_tree_hash_marks_a_newer_skill_folder(self) -> None:
        calls = []

        def fetch(url):
            calls.append(url)
            self.assertTrue(url.startswith("https://api.github.com/repos/example/demo/git/trees/"))
            self.assertNotIn("token", url.lower())
            return 200, tree("skills/demo", NEW), ""

        report = self.check(
            [self.cli_item()],
            {"demo": self.lock_entry()},
            fetch,
        )
        self.assertEqual(report.state, "ok")
        self.assertEqual(report.updates["demo"]["latest"], NEW)
        self.assertEqual(report.updates["demo"]["via"], "github")
        self.assertEqual(len(calls), 1)
        self.assertIn("HEAD", calls[0])

    def test_matching_tree_hash_is_current(self) -> None:
        report = self.check(
            [self.cli_item()],
            {"demo": self.lock_entry()},
            lambda url: (200, tree("skills/demo", OLD), ""),
        )
        self.assertEqual(report.state, "ok")
        self.assertEqual(report.updates, {})
        self.assertIn("没有更新", report.detail)
        self.assertEqual(report.checked, 1)

    def test_network_failure_is_not_reported_as_no_update(self) -> None:
        report = self.check(
            [self.cli_item()],
            {"demo": self.lock_entry()},
            lambda url: (0, None, "network"),
        )
        self.assertEqual(report.state, "fallback")
        self.assertEqual(report.updates, {})
        self.assertEqual(report.detail, "上游请求失败，不能判断有没有更新")

    def test_truncated_tree_is_not_current(self) -> None:
        report = self.check(
            [self.cli_item()],
            {"demo": self.lock_entry()},
            lambda url: (200, tree("skills/demo", OLD, truncated=True), ""),
        )
        self.assertEqual(report.state, "fallback")
        self.assertEqual(report.updates, {})

    def test_partial_failure_keeps_a_confirmed_update(self) -> None:
        def fetch(url):
            if "/example/old/" in url:
                return 0, None, "network"
            return 200, tree("skills/new", NEW), ""

        items = [
            self.cli_item(name="old", source="example/old"),
            self.cli_item(name="new", source="example/new", path="skills/new/SKILL.md"),
        ]
        lock = {
            "old": self.lock_entry(source="example/old"),
            "new": self.lock_entry(source="example/new", path="skills/new/SKILL.md"),
        }
        report = self.check(items, lock, fetch)
        self.assertEqual(report.state, "fallback")
        self.assertIn("new", report.updates)
        self.assertNotIn("old", report.updates)
        self.assertIn("已确认 1 个可升级", report.detail)

    def test_empty_lock_hash_is_skipped_not_current(self) -> None:
        calls = []
        items = [self.cli_item(name="blank", version=None), self.cli_item()]
        lock = {
            "blank": self.lock_entry(folder_hash=""),
            "demo": self.lock_entry(),
        }
        report = self.check(items, lock, lambda url: calls.append(url) or (200, tree("skills/demo", OLD), ""))
        self.assertEqual(report.state, "ok")
        self.assertEqual(report.updates, {})
        self.assertGreaterEqual(report.skipped, 1)
        self.assertTrue(calls)
        self.assertTrue(all("/example/demo/" in url for url in calls))

    def test_npm_latest_uses_the_registry_and_compares_versions(self) -> None:
        calls = []

        def fetch(url):
            calls.append(url)
            return 200, {"version": "0.163.0", "dist": {"tarball": "https://example.invalid/x.tgz"}}, ""

        report = self.check([self.codex_item("0.162.1")], fetch=fetch)
        self.assertEqual(calls, [f"https://registry.npmjs.org/{quote('@openai/codex', safe='@')}/latest"])
        self.assertEqual(report.updates["codex"]["latest"], "0.163.0")
        self.assertEqual(report.updates["codex"]["via"], "npm")
        same = self.check([self.codex_item("0.163.0")], fetch=fetch)
        self.assertEqual(same.updates, {})
        self.assertEqual(same.state, "ok")

    def test_release_falls_back_to_tags_and_ignores_prerelease_when_older(self) -> None:
        def fetch(url):
            if url.endswith("/releases/latest"):
                return 404, None, ""
            return 200, [{"name": "v1.2.3"}, {"name": "v1.2.3-rc.2"}, {"name": "not-a-version"}], ""

        report = self.check([self.github_release_item("1.2.3")], fetch=fetch)
        self.assertEqual(report.updates, {})
        clear_cache()
        newer = self.check(
            [self.github_release_item("1.2.3")],
            fetch=lambda url: (200, {"tag_name": "v1.10.0"}, "") if url.endswith("/releases/latest") else (500, None, ""),
        )
        self.assertEqual(newer.updates["demo"]["latest"], "v1.10.0")

    def test_plugin_marketplace_repo_is_not_queried(self) -> None:
        manifest = self.root / "plugin.json"
        manifest.write_text(
            json.dumps({"name": "cloudflare", "version": "0.1.2", "repository": "https://github.com/openai/plugins"}),
            encoding="utf-8",
        )
        calls = []
        item = self.plugin_item("cloudflare", "plugin:codex/cloudflare@openai-curated", manifest)
        report = self.check([item], fetch=lambda url: calls.append(url) or (200, {"tag_name": "v9.9.9"}, ""))
        self.assertEqual(calls, [])
        self.assertEqual(report.updates, {})
        self.assertEqual(report.state, "fallback")
        self.assertIn("不能当成没有更新", report.detail)

    def test_plugin_repo_named_like_the_plugin_uses_releases(self) -> None:
        manifest = self.root / "plugin.json"
        manifest.write_text(
            json.dumps(
                {
                    "name": "understand-anything",
                    "version": "2.8.1",
                    "repository": {"type": "git", "url": "https://github.com/Egonex-AI/Understand-Anything.git"},
                }
            ),
            encoding="utf-8",
        )
        item = self.plugin_item(
            "understand",
            "plugin:cursor/understand-anything@understand-anything",
            manifest,
            "2.8.1",
        )
        report = self.check([item], fetch=lambda url: (200, {"tag_name": "v2.9.0"}, ""))
        self.assertEqual(report.updates["understand"]["latest"], "v2.9.0")
        self.assertEqual(report.state, "ok")

    def test_local_skill_is_not_an_upstream_candidate(self) -> None:
        calls = []
        item = {"name": "local", "source_class": "local_custom", "baseline_ref": "sha256:" + "e" * 64, "current_version": "1.0.0"}
        report = self.check([item], fetch=lambda url: calls.append(url) or (200, {"version": "9.0.0"}, ""))
        self.assertEqual(calls, [])
        self.assertEqual(report.state, "ok")
        self.assertIn("没有需要查的上游", report.detail)

    def test_results_are_cached_and_requests_are_capped(self) -> None:
        clock = Clock()
        calls = []

        def fetch(url):
            calls.append(url)
            name = url.split("/repos/example/")[1].split("/")[0]
            return 200, tree(f"skills/{name}", OLD), ""

        items = [self.cli_item(name=name, source=f"example/{name}", path=f"skills/{name}/SKILL.md") for name in ("one", "two")]
        lock = {name: self.lock_entry(source=f"example/{name}", path=f"skills/{name}/SKILL.md") for name in ("one", "two")}
        first = self.check(items, lock, fetch, mono=clock)
        self.assertEqual(len(calls), 2)
        clock.now += 10
        second = self.check(items, lock, fetch, mono=clock)
        self.assertEqual(len(calls), 2)
        self.assertEqual(first.updates.keys(), second.updates.keys())
        clock.now += skill_upgrade.SUCCESS_TTL + 1
        calls.clear()
        capped = self.check(items, lock, fetch, mono=clock, max_requests=1)
        self.assertEqual(len(calls), 1)
        self.assertEqual(capped.state, "fallback")
        self.assertIn("请求上限", capped.detail)

    def test_requests_are_spaced(self) -> None:
        slept = []
        items = [self.cli_item(name=name, source=f"example/{name}", path=f"skills/{name}/SKILL.md") for name in ("one", "two")]
        lock = {name: self.lock_entry(source=f"example/{name}", path=f"skills/{name}/SKILL.md") for name in ("one", "two")}
        self.check(
            items,
            lock,
            lambda url: (200, tree("skills/one", OLD), ""),
            min_interval=0.2,
            sleep=lambda seconds: slept.append(seconds),
        )
        self.assertEqual(slept, [0.2])

    def test_root_skill_uses_the_repository_tree_sha(self) -> None:
        report = self.check(
            [self.cli_item(path="SKILL.md")],
            {"demo": self.lock_entry(path="SKILL.md")},
            lambda url: (200, tree("unused", "e" * 40, root=NEW), ""),
        )
        self.assertEqual(report.updates["demo"]["latest"], NEW)

    def test_fetch_rejects_other_hosts_and_sends_no_credentials(self) -> None:
        opened = []

        class Response:
            status = 200

            def geturl(self):
                return "https://registry.npmjs.org/@openai%2Fcodex/latest"

            def read(self, _limit):
                return b'{"version":"0.163.0"}'

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        def opener(request, timeout=None):
            opened.append((request, timeout))
            return Response()

        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "secret-token", "GH_TOKEN": "secret-token"}):
            with mock.patch.object(skill_upgrade._OPENER, "open", side_effect=opener):
                blocked = default_fetch("http://example.com/latest")
                self.assertEqual(blocked, (0, None, "blocked"))
                self.assertEqual(opened, [])
                status, body, error = default_fetch("https://registry.npmjs.org/@openai%2Fcodex/latest")
        self.assertEqual((status, error, body["version"]), (200, "", "0.163.0"))
        request, timeout = opened[0]
        self.assertEqual(timeout, 8)
        headers = {key.lower(): value for key, value in request.header_items()}
        self.assertNotIn("authorization", headers)
        self.assertEqual(headers.get("user-agent"), "agents-lab-board")
        self.assertNotIn("secret-token", str(request.header_items()))

    def test_payload_fills_the_bucket_without_writing_the_skill_or_lock(self) -> None:
        skill_dir = self.repo / "skills" / "demo"
        skill_dir.mkdir(parents=True)
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text("---\nname: demo\n---\n\nbody\n", encoding="utf-8")
        lock_path = self.root / "skill-lock.json"
        lock_path.write_text(json.dumps({"version": 3, "skills": {"demo": self.lock_entry()}}), encoding="utf-8")
        before_lock = lock_path.read_bytes()
        before_skill = skill_md.read_bytes()
        body = self.payload(fetch=lambda url: (200, tree("skills/demo", NEW), ""))
        item = body["items"][0]
        source = next(source for source in body["meta"]["sources"] if source["id"] == "upgrade")
        self.assertEqual(item["bucket"], "upgradable")
        self.assertEqual(item["reason"], f"{OLD[:7]} → {NEW[:7]}")
        self.assertEqual(item["tags"], [])
        self.assertEqual(source["state"], "ok")
        self.assertTrue(body["attention"]["buckets"]["upgradable"]["connected"])
        self.assertEqual(body["attention"]["counts"]["upgradable"], 1)
        self.assertEqual(lock_path.read_bytes(), before_lock)
        self.assertEqual(skill_md.read_bytes(), before_skill)

    def test_payload_network_failure_stays_disconnected(self) -> None:
        skill_dir = self.repo / "skills" / "demo"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\nname: demo\n---\n\n", encoding="utf-8")
        lock_path = self.root / "skill-lock.json"
        lock_path.write_text(json.dumps({"version": 3, "skills": {"demo": self.lock_entry()}}), encoding="utf-8")
        body = self.payload(fetch=lambda url: (0, None, "network"))
        bucket = body["attention"]["buckets"]["upgradable"]
        source = next(source for source in body["meta"]["sources"] if source["id"] == "upgrade")
        self.assertEqual(source["state"], "fallback")
        self.assertFalse(bucket["connected"])
        self.assertEqual(bucket["items"], [])
        self.assertIn("不能判断有没有更新", bucket["reason"])
        self.assertNotEqual(body["items"][0]["bucket"], "upgradable")

    def cli_item(self, name="demo", source="example/demo", path="skills/demo/SKILL.md", version=OLD):
        return {
            "name": name,
            "source_class": "skills_cli_managed",
            "baseline_ref": f"github:{source}",
            "baseline_subject": path,
            "current_version": version,
            "observed_version": version,
        }

    def lock_entry(self, source="example/demo", path="skills/demo/SKILL.md", folder_hash=OLD):
        return {
            "source": source,
            "sourceType": "github",
            "sourceUrl": f"https://github.com/{source}.git",
            "skillPath": path,
            "skillFolderHash": folder_hash,
        }

    def codex_item(self, version: str) -> dict:
        return {
            "name": "codex",
            "source_class": "runtime_builtin",
            "baseline_ref": "runtime:codex",
            "current_version": version,
            "observed_version": version,
        }

    def github_release_item(self, version: str) -> dict:
        return {
            "name": "demo",
            "source_class": "third_party_unmanaged",
            "baseline_ref": "github:example/demo",
            "current_version": version,
            "observed_version": version,
        }

    def plugin_item(self, name: str, baseline_ref: str, manifest: Path, version: str = "0.1.2") -> dict:
        return {
            "name": name,
            "source_class": "runtime_plugin",
            "baseline_ref": baseline_ref,
            "baseline_subject": str(manifest),
            "current_version": version,
            "observed_version": version,
        }

    def payload(self, fetch):
        inventory = self.root / "skills-inventory.json"
        record = {
            "name": "demo",
            "source_class": "skills_cli_managed",
            "authority": {
                "ownership": "third_party",
                "source_authority": "upstream_repository",
                "update_authority": "skills_cli",
                "canonical_path": None,
            },
            "baseline": {"kind": "upstream_ref", "subject": "skills/demo/SKILL.md"},
            "adapters": [],
            "status": "active",
            "review_time": None,
            "baseline_ref": "github:example/demo",
            "current_version": OLD,
            "scope": "machine",
            "customization_mode": "none",
            "compatibility_constraints": [],
            "conflict_status": "unreviewed",
            "visible_paths": ["skills/demo"],
        }
        inventory.write_text(json.dumps({"skills": [record]}), encoding="utf-8")
        tools = self.root / "tool-roots.json"
        tools.write_text(json.dumps({"tools": [], "roots": []}), encoding="utf-8")
        staging = self.root / "staging"
        staging.mkdir(exist_ok=True)
        return build_payload(
            repo_root=self.repo,
            inventory_path=inventory,
            tool_roots_path=tools,
            staging_root=staging,
            lock_path=self.root / "skill-lock.json",
            changelog_dir=None,
            now=NOW,
            scan_roots=[],
            upgrade_fetch=fetch,
        )


if __name__ == "__main__":
    unittest.main()
