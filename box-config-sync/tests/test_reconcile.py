"""ACM-927: ordered state table, mapping and canonical instructions goldens."""
import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from support import PROJECT
from boxsync import reconcile as rec
from boxsync.domains.workflows import frontmatter

NOW = datetime(2026, 11, 16, 12, tzinfo=timezone.utc)
STAMP = NOW.isoformat()
A, B, C = "a" * 64, "b" * 64, "c" * 64


def skill(directory="example", digest=A, name="示例技能", **extra):
    return {"dir_name": directory, "name": name, "tree_sha256": digest,
            "tree_state": "observed", "body_hash_state": "observed", **extra}


def leg(*rows, state="ok"):
    return {"state": state, "items": list(rows)}


def compare(live, git, board=None, **kwargs):
    return rec.reconcile_workflows(live, git, board, collected_at=kwargs.pop("collected_at", STAMP),
                                   now=NOW, **kwargs)


class WorkflowsTests(unittest.TestCase):
    def test_six_states_fixture(self):
        fixture = json.loads((Path(__file__).parent / "fixtures/reconcile-states.json").read_text())
        for case in fixture:
            with self.subTest(case=case["expected"]):
                rows = compare(case["live"], case["git"], case["board"])
                self.assertEqual(rows[0]["state"], case["expected"])
        self.assertEqual({case["expected"] for case in fixture}, {
            "unknown", "missing_on_box", "missing_in_git", "orphan_export", "content_drift", "aligned"})

    def test_read_failures_never_become_missing(self):
        failures = ("denied", "too_large", "unsupported_entry", "changed_during_read", "error",
                    "timeout", "partial", "not_collected", "not_regular", "uncollected")
        for failure in failures:
            for side in range(3):
                for present in (False, True):
                    legs = [leg(skill()), leg(skill()), leg(skill())]
                    legs[side] = leg(*([skill(tree_state=failure)] if present else []),
                                     state="ok" if present else failure)
                    with self.subTest(failure=failure, side=side, present=present):
                        self.assertEqual(compare(*legs)[0]["state"], "unknown")

    def test_failed_or_incomplete_body_is_unknown(self):
        for extra in ({"body_hash_state": "missing"}, {"tree_sha256": None},
                      {"files_truncated": True}, {"files": [{"path": "a", "state": "error"}]}):
            self.assertEqual(compare(leg(skill(**extra)), leg())[0]["state"], "unknown")

    def test_stale_overrides_every_workflow_and_boundary_is_fresh(self):
        live = leg(skill("aligned"), skill("local"))
        git = leg(skill("aligned"), skill("git-only"))
        board = leg(skill("export"))
        for seconds, stale in ((36 * 3600, False), (36 * 3600 + 1, True)):
            rows = compare(live, git, board, collected_at=(NOW - timedelta(seconds=seconds)).isoformat())
            self.assertEqual(all(row["state"] == "unknown" for row in rows), stale)
            if stale:
                self.assertTrue(all("snapshot_stale" in row["reason_codes"] for row in rows))
        for stamp in (None, "bad", "2026-11-16T12:00:00", (NOW + timedelta(seconds=1)).isoformat()):
            self.assertEqual(compare(live, git, board, collected_at=stamp)[0]["state"], "unknown")

    def test_numeric_chinese_and_rename_keep_directory_identity(self):
        directory = "skill-1793256615882"
        name, flags = frontmatter("---\nname: '店铺  内容口径审稿'\n---\n正文".encode())
        self.assertEqual((name, flags), ("店铺 内容口径审稿", []))
        rows = compare(leg(skill(directory, B, name)), leg(skill(directory, A, "旧名字")))
        self.assertEqual(rows[0]["id"], "grok-workflow:" + directory)
        self.assertEqual(rows[0]["kind"], "numeric")
        self.assertTrue(rows[0]["name_changed"])
        self.assertEqual(rows[0]["state"], "content_drift")
        self.assertEqual(rows[0]["export_state"], "not_visible_on_box")

    def test_legacy_join_order_and_duplicate_names(self):
        live = leg(skill("exact", name="同名"), skill("numeric", name="Cafe\u0301  检查"),
                   skill("dup-a", name="重复"), skill("dup-b", name="重复"), skill("unmatched"))
        board = {"skills_user": {"skills": [
            {"slug": "exact", "name": "不同名", "tree_sha256": A},
            {"slug": "manual-slug", "name": "CAFÉ 检查", "tree_sha256": A},
            {"slug": "old-duplicate", "name": "重复", "tree_sha256": A}]}}
        rows = {r["dir_name"]: r for r in compare(live, live, board)}
        self.assertEqual(rows["exact"]["join_basis"], "dir_name")
        self.assertEqual(rows["numeric"]["join_basis"], "name")
        self.assertNotIn("manual-slug", rows)
        self.assertEqual(rows["unmatched"]["join_basis"], "none")
        for directory in ("dup-a", "dup-b"):
            self.assertTrue(rows[directory]["duplicate_name"])
            self.assertEqual(rows[directory]["join_basis"], "none")
        self.assertEqual(rows["old-duplicate"]["state"], "orphan_export")

    def test_duplicate_export_names_cannot_associate(self):
        board = {"skills_user": {"skills": [
            {"slug": slug, "name": "示例技能", "tree_sha256": A} for slug in ("one", "two")]}}
        rows = compare(leg(skill()), leg(skill()), board)
        self.assertEqual(rows[0]["join_basis"], "none")
        self.assertTrue(rows[0]["duplicate_name"])

    def test_invalid_and_duplicate_directory_are_unknown(self):
        for directory in (".hidden", "有中文", "has space", "a" * 129, "../escape"):
            row = compare(leg(skill(directory)), leg())[0]
            self.assertEqual(row["state"], "unknown")
            self.assertIn("invalid_dir_name", row["reason_codes"])
        self.assertEqual(compare(leg(skill(), skill()), leg())[0]["state"], "unknown")

    def test_export_annotations_do_not_change_primary_state(self):
        for digest, side in ((A, "matches_live"), (B, "matches_git"), (C, "differs")):
            row = compare(leg(skill(digest=A)), leg(skill(digest=B)), leg(skill(digest=digest)))[0]
            self.assertEqual((row["state"], row["export_side"]), ("content_drift", side))
        row = compare(leg(skill()), leg(skill()), leg(skill(digest=B)))[0]
        self.assertEqual((row["state"], row["export_state"]), ("aligned", "stale"))

    def test_file_diff_and_purity(self):
        live = leg(skill(files=[{"path": "SKILL.md", "sha256": A}, {"path": "new", "sha256": B}]))
        git = leg(skill(digest=B, files=[{"path": "SKILL.md", "sha256": B}, {"path": "old", "sha256": C}]))
        before = copy.deepcopy((live, git))
        with patch("builtins.open", side_effect=AssertionError("I/O")), patch("subprocess.run", side_effect=AssertionError("process")):
            a, b = compare(live, git), compare(live, git)
        self.assertEqual(a, b)
        self.assertEqual((live, git), before)
        self.assertEqual([f["path"] for f in a[0]["file_differences"]], ["SKILL.md", "new", "old"])

    def test_snapshot_partial_never_proves_absence(self):
        snapshot = {"collected_at": STAMP, "sources": {"box_disk": "partial"}, "domains": {"workflows": []}}
        report = rec.reconcile(snapshot, leg(skill()), now=NOW)
        self.assertEqual(report["summary"], {"unknown": 1})


class InstructionsGoldenTests(unittest.TestCase):
    def test_rows_equal_canonical_matrix_with_real_source_fixtures(self):
        canonical = rec._query
        source = canonical.source
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sources").mkdir()
            agents, bots = [], []
            # All five canonical drift outcomes, missing legs, and separate workspaces.
            for i, (g, b, l, withheld) in enumerate((
                    ("same", "same", "same", False), ("g", "b", "g", False),
                    ("g", "b", "l", False), ("g", "g", "l", False),
                    ("secret", "secret", "secret", True), ("g", None, "g", False))):
                path = f"bot-{i}.md"
                (root / "sources" / path).write_text(g)
                bots.append({"id": str(i), "path": path, "withheld": withheld})
                agents.append({"id": str(i), "workspace_id": "w", "path": path, "withheld": withheld})
            (root / "sources/index.json").write_text(json.dumps({"agents": agents, "bots": bots}))
            (root / "grok-bots-manifest.json").write_text(json.dumps({"bots": []}))
            board = {"agents": {}, "bots": {}}
            live_a, live_b = [], []
            cases = ("same", "g", "l", "l", "secret", "g")
            for i, text in enumerate(cases):
                live_a.append({"id": str(i), "workspace_id": "w", "instructions": text})
                live_b.append({"id": str(i), "description": text})
                if i != 5:
                    value = ("same", "b", "b", "g", "secret")[i]
                    row = {"text": value, "sha256": source.instructions_sha256(value)}
                    board["agents"]["w/" + str(i)] = row
                    board["bots"][str(i)] = row
            git = {"agents": source.git_agents(root), "bots": source.git_bots(root)}
            for fresh in ({"board": "fresh", "agent": "fresh", "bot": "fresh"},
                          {"board": "stale", "agent": "unknown", "bot": "stale"}):
                kwargs = {"observed_at": {"agent": STAMP, "bot": STAMP}, "freshness": fresh}
                expected = canonical.matrix(root, board, live_a, live_b, **kwargs)
                before = copy.deepcopy((git, board, live_a, live_b))
                with patch("builtins.open", side_effect=AssertionError("I/O")), patch("subprocess.run", side_effect=AssertionError("process")):
                    actual = rec.reconcile_instructions(git, board, live_a, live_b, **kwargs)
                self.assertEqual(actual, expected)
                self.assertEqual((git, board, live_a, live_b), before)
            self.assertEqual({r["drift"] for r in canonical.matrix(root, board, live_a, live_b,
                             observed_at={}, freshness={"agent": "fresh", "bot": "fresh"})["items"]},
                             {"aligned", "stale", "split", "modified", "withheld"})


if __name__ == "__main__":
    unittest.main()
