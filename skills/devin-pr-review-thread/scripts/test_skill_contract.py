#!/usr/bin/env python3
import pathlib


SKILL_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_consumers_wait_for_the_original_queue_client_process():
    skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    scenarios = (SKILL_ROOT / "pressure-scenarios.md").read_text(encoding="utf-8")

    assert "same queue client process" in skill
    assert "absence of intermediate stdout" in skill
    assert "must not be interpreted as a provider failure" in skill
    assert "terminal-inspection attempt fails" in scenarios
    assert "wait for that same process" in scenarios


if __name__ == "__main__":
    test_consumers_wait_for_the_original_queue_client_process()
    print("ok")
