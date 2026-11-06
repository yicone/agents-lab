"""Board-facing shim. The status bridge and Board share one decision module.

Canonical source: multica-bridge/agent_delivery.py
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_CANONICAL = Path(__file__).resolve().parents[3] / "multica-bridge" / "agent_delivery.py"
_spec = importlib.util.spec_from_file_location("multica_bridge_agent_delivery", _CANONICAL)
if _spec is None or _spec.loader is None:
    raise ImportError(f"canonical agent_delivery missing: {_CANONICAL.name}")
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

SHADOW_STATUS = _mod.SHADOW_STATUS
build_change = _mod.build_change
collect_for_issue = _mod.collect_for_issue
decide_delivery = _mod.decide_delivery
is_pure_ack = _mod.is_pure_ack
is_quiet = _mod.is_quiet
is_system_comment = _mod.is_system_comment
normalize_text = _mod.normalize_text
parse_time = _mod.parse_time
remember_shadow = _mod.remember_shadow
route_policy = _mod.route_policy
