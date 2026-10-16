"""ACM-129, ACM-139, and ACM-140 shapes for uncovered agent delivery."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from multica_board.agent_delivery import (  # noqa: E402
    SHADOW_STATUS,
    collect_for_issue,
    decide_delivery,
    is_pure_ack,
    remember_shadow,
    route_policy,
)

BUILDER = "builder"
T_COMMENT = "2026-10-15T08:20:08Z"
T_RUN = "2026-10-15T08:21:09Z"
T_REVIEW = "2026-10-16T00:49:13Z"
T_PR = "2026-10-16T02:00:46Z"
T_ENTERED = "2026-10-16T01:39:26Z"


def issue(**kw):
    base = {
        "issue_id": "issue-1",
        "identifier": "ACM-129",
        "title": "gate",
        "status": "in_progress",
        "status_category": "in_progress",
        "assignee_type": "agent",
        "assignee_id": BUILDER,
        "workspace_slug": "acm-lab",
        "workspace_id": "ws",
        "project_id": "proj",
        "comments": [],
        "runs": [],
        "transitions": [],
        "same_tick_covered": False,
    }
    base.update(kw)
    return base


def comment(cid, at, content, *, new=True, author=BUILDER):
    return {
        "id": cid, "author_type": "agent", "author_id": author,
        "created_at": at, "content": content, "is_new": new,
    }


def run(rid, at, *, new=True, agent=BUILDER, status="completed"):
    return {
        "id": rid, "agent_id": agent, "status": status,
        "completed_at": at, "is_new": new,
    }


class DecideTests(unittest.TestCase):
    def test_acm_129_approval_request_without_status_change(self):
        found = collect_for_issue(issue(
            comments=[comment("c129", T_COMMENT, "本地门禁已完成，请批准激活。")],
            runs=[run("r129", T_RUN)],
        ))
        self.assertEqual(found["delivery"], "send")
        self.assertEqual(found["reason"], "assignee_comment")
        self.assertEqual(found["comment_id"], "c129")
        self.assertEqual(found["run_id"], "r129")
        self.assertEqual(found["event_id"], "agent_delivery:c129")

    def test_acm_129_handoff_one_second_later_is_covered(self):
        found = collect_for_issue(issue(
            status="in_review",
            comments=[comment("c2", "2026-10-16T00:49:12Z", "已按批准激活。")],
            runs=[run("r2", "2026-10-16T00:49:13Z")],
            transitions=[{"to": "in_review", "at": T_REVIEW}],
        ))
        self.assertIsNone(found)

    def test_acm_139_new_delivery_while_already_in_review(self):
        found = collect_for_issue(issue(
            identifier="ACM-139",
            status="in_review",
            status_category="in_review",
            comments=[comment("c139", T_PR, "已推送 https://github.com/yicone/agents-lab/pull/20")],
            runs=[run("r139", "2026-10-16T02:00:55Z")],
            transitions=[{"to": "in_review", "at": T_ENTERED}],
        ))
        self.assertEqual(found["reason"], "assignee_comment")
        self.assertEqual(found["comment_id"], "c139")
        self.assertEqual(found["event_id"], "agent_delivery:c139")

    def test_acm_140_same_shape(self):
        found = collect_for_issue(issue(
            identifier="ACM-140",
            status="in_review",
            status_category="in_review",
            comments=[comment("c140", "2026-10-16T01:21:48Z", "PR #18 已推送，请看。")],
            runs=[run("r140", "2026-10-16T01:21:48Z")],
            transitions=[{"to": "in_review", "at": "2026-10-16T01:00:00Z"}],
        ))
        self.assertEqual(found["comment_id"], "c140")
        self.assertEqual(found["run_id"], "r140")

    def test_same_tick_status_reminder_suppresses_the_comment(self):
        found = collect_for_issue(issue(
            same_tick_covered=True,
            comments=[comment("c", T_COMMENT, "请批准。")],
            runs=[run("r", T_RUN)],
        ))
        self.assertIsNone(found)

    def test_run_fallback_when_there_is_no_comment_and_no_transition(self):
        found = collect_for_issue(issue(runs=[run("r-only", T_RUN)]))
        self.assertEqual(found["reason"], "run_completed")
        self.assertEqual(found["event_id"], "agent_delivery:run:r-only")
        self.assertIsNone(found["comment_id"])
        self.assertIsNone(found["content"])

    def test_ack_and_duplicate_are_not_deliveries(self):
        self.assertTrue(is_pure_ack("确认。"))
        self.assertTrue(is_pure_ack("  ok "))
        self.assertFalse(is_pure_ack("确认，请看 PR #18"))
        ack = collect_for_issue(issue(
            comments=[comment("a", T_COMMENT, "确认")],
            runs=[run("r", T_RUN)],
        ))
        self.assertIsNone(ack)
        dup = collect_for_issue(issue(comments=[
            comment("old", "2026-10-15T08:00:00Z", "本地门禁已完成，请批准激活。", new=False),
            comment("again", T_COMMENT, "本地门禁已完成，请批准激活。"),
        ]))
        self.assertIsNone(dup)

    def test_quiet_statuses_and_other_authors_stay_quiet(self):
        for status in ("done", "cancelled", "canceled", "backlog"):
            found = collect_for_issue(issue(
                status=status,
                comments=[comment("c", T_COMMENT, "还有一件新交付。")],
                runs=[run("r", T_RUN)],
            ))
            self.assertIsNone(found, status)
        other = collect_for_issue(issue(
            comments=[comment("c", T_COMMENT, "我是协作者的结论。", author="vega")],
            runs=[run("r", T_RUN, agent="vega")],
        ))
        self.assertIsNone(other)
        member = collect_for_issue(issue(
            assignee_type="member",
            comments=[comment("c", T_COMMENT, "请看这个。")],
        ))
        self.assertIsNone(member)

    def test_uncertain_text_is_kept(self):
        decision = decide_delivery(
            status="in_progress",
            assignee_type="agent",
            assignee_id=BUILDER,
            comments=[comment("c", T_COMMENT, "收到，不过激活脚本还差一步。")],
            runs=[],
            transitions=[],
        )
        self.assertEqual(decision["action"], "notify")
        self.assertEqual(decision["reason"], "assignee_comment")

    def test_other_workspaces_shadow_and_can_be_turned_off(self):
        self.assertEqual(route_policy("acm-lab", {}), "send")
        self.assertEqual(route_policy("clipvibe", {}), "shadow")
        self.assertEqual(route_policy("acm-lab", {"agent_delivery": "off"}), "off")
        self.assertEqual(route_policy("clipvibe", {"agent_delivery": "send"}), "send")
        shadowed = collect_for_issue(issue(
            workspace_slug="clipvibe",
            comments=[comment("c", T_COMMENT, "请批准这一版。")],
        ), route={})
        self.assertEqual(shadowed["delivery"], "shadow")
        box = {"items": {}}
        self.assertEqual(remember_shadow(box, shadowed, "2026-10-16T02:00:00Z"), "shadowed")
        self.assertEqual(box["items"][shadowed["event_id"]]["status"], SHADOW_STATUS)
        self.assertEqual(remember_shadow(box, shadowed, "2026-10-16T02:01:00Z"), "duplicate")
        self.assertIsNone(collect_for_issue(issue(
            comments=[comment("c", T_COMMENT, "请批准。")],
        ), route={"agent_delivery": "off"}))


if __name__ == "__main__":
    unittest.main()
