"""The shared disk/turn configuration fingerprint projection.

Identity is compared separately using owner_id/routine_id. Only name, enabled,
schedule and prompt_sha256 describe comparable configuration. Missing optional
values become null; text follows the disk collector's existing NFC/whitespace
normalization. An absent enabled flag stays unknown (null), distinct from false.
updated_at, last_run_at, next_run_at and capture/evidence metadata never enter
this fingerprint. The projection does not rewrite exported snapshot fields.
"""
from .domains.common import label
from .hashing import config_fingerprint


def routine_fingerprint(routine):
    return config_fingerprint({
        "name": label(routine.get("name")),
        "enabled": routine.get("enabled") if type(routine.get("enabled")) is bool else None,
        "schedule": label(routine.get("schedule"), 120),
        "prompt_sha256": routine.get("prompt_sha256"),
    })
