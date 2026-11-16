"""Pure Live/Git/optional Board comparison. Inputs contain metadata, never paths to read.

A workflow leg is {state: 'ok'|'absent'|<failure>, items: [workflow rows]}.
Only a complete inventory proves that an omitted directory is absent. Board may
instead contain a legacy skills_user.skills manifest; None means invisible on Box.
Time is explicit so replaying fixtures is deterministic.
"""
from collections import Counter
from datetime import datetime
import importlib.util
from pathlib import Path
import re
import unicodedata

from . import instructions  # Establish the canonical diff_instructions import.

_spec = importlib.util.spec_from_file_location(
    "boxsync_query_instructions", Path(__file__).resolve().parents[2]
    / "instructions-governance/scripts/query_instructions.py")
_query = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_query)

DIR = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
SHA = re.compile(r"[0-9a-f]{64}\Z")


def _name(value):
    return " ".join(unicodedata.normalize("NFC", value).split()) if isinstance(value, str) else None


def _key(row):
    name = _name(row.get("name"))
    return name.casefold() if name else None


def _inventory(leg, legacy=False):
    items = leg.get("items", [])
    if legacy:
        items = leg["skills_user"]["skills"]
    result = {}
    duplicates = set()
    for item in items:
        row = dict(item)
        directory = row.get("slug") if legacy else row.get("dir_name")
        if not isinstance(directory, str):
            raise ValueError("missing directory identity")
        row["dir_name"] = directory
        if directory in result:
            duplicates.add(directory)
        result[directory] = row
    return result, duplicates


def _failure(leg, row):
    state = leg.get("state", "uncollected")
    if state not in ("ok", "absent"):
        return state
    if row is None:
        return None
    for field in ("tree_state", "body_hash_state"):
        state = row.get(field, "observed")
        if state != "observed":
            return state
    if row.get("files_truncated"):
        return "too_large"
    if any(f.get("state", "observed") != "observed" for f in row.get("files", [])):
        return "file_read_failed"
    if not isinstance(row.get("tree_sha256"), str) or not SHA.fullmatch(row["tree_sha256"]):
        return "hash_missing"
    return None


def _stamp(value):
    if not isinstance(value, str):
        raise ValueError("missing timestamp")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("timestamp must have timezone")
    return result


def reconcile_workflows(live, git, board=None, *, collected_at, now, max_age=36 * 3600):
    """Return deterministic §4.4 rows; never infer absence from a failed read."""
    if max_age < 0 or now.tzinfo is None:
        raise ValueError("invalid age or clock")
    try:
        age = (now - _stamp(collected_at)).total_seconds()
        clock_reason = "snapshot_stale" if age > max_age else "snapshot_future" if age < 0 else None
    except (TypeError, ValueError):
        clock_reason = "snapshot_time_invalid"
    left, ld = _inventory(live)
    right, rd = _inventory(git)
    legacy = board is not None and "skills_user" in board
    exported, bd = _inventory(board, legacy) if board is not None else ({}, set())
    board_leg = {"state": "ok"} if legacy else board
    keys = {}
    for directory in left.keys() | right.keys():
        for row in (left.get(directory), right.get(directory)):
            if row and _key(row):
                keys.setdefault(_key(row), set()).add(directory)
    export_counts = Counter(_key(row) for row in exported.values() if _key(row))
    matches = {}
    used = set()
    # Exact directory matches always win over name associations.
    for directory in sorted(left.keys() | right.keys()):
        if directory in exported:
            matches[directory] = (directory, "dir_name")
            used.add(directory)
    if legacy:
        for directory in sorted((left.keys() | right.keys()) - matches.keys()):
            row = left.get(directory) or right[directory]
            name = _key(row)
            if not name or len(keys[name]) != 1 or export_counts[name] != 1:
                continue
            candidate = next(k for k, v in exported.items() if _key(v) == name)
            if candidate not in used:
                matches[directory] = (candidate, "name")
                used.add(candidate)
    rows = []
    identities = (left.keys() | right.keys()) | (exported.keys() - used)
    for directory in sorted(identities):
        lrow, grow = left.get(directory), right.get(directory)
        export_id, basis = matches.get(directory, (directory, "none"))
        brow = exported.get(export_id)
        duplicate = any(len(keys.get(_key(row), set())) > 1 or export_counts[_key(row)] > 1
                        or "duplicate_name" in row.get("flags", [])
                        for row in (lrow, grow, brow) if row)
        reasons = []
        if clock_reason:
            reasons.append(clock_reason)
        if not DIR.fullmatch(directory):
            reasons.append("invalid_dir_name")
        if directory in ld | rd or export_id in bd:
            reasons.append("duplicate_dir_name")
        for side, leg, row in (("live", live, lrow), ("git", git, grow), ("board", board_leg, brow)):
            if leg is not None:
                failure = _failure(leg, row)
                if failure:
                    reasons.append(side + "_" + failure)
        if reasons:
            state = "unknown"
        elif lrow is None and grow is not None:
            state = "missing_on_box"
        elif lrow is not None and grow is None:
            state = "missing_in_git"
        elif lrow is None and grow is None:
            state = "orphan_export"
        elif lrow["tree_sha256"] != grow["tree_sha256"]:
            state = "content_drift"
        else:
            state = "aligned"
        lh = (lrow or {}).get("tree_sha256")
        gh = (grow or {}).get("tree_sha256")
        bh = (brow or {}).get("tree_sha256")
        export_state = ("not_visible_on_box" if board is None else "absent" if brow is None
                        else "aligned" if bh and bh == lh == gh else "stale")
        export_side = ("absent" if brow is None else "matches_live" if bh and bh == lh
                       else "matches_git" if bh and bh == gh else "differs")
        files = []
        if state == "content_drift":
            lf = {f["path"]: f for f in lrow.get("files", [])}
            gf = {f["path"]: f for f in grow.get("files", [])}
            for path in sorted(lf.keys() | gf.keys()):
                a, b = lf.get(path), gf.get(path)
                if a is None or b is None or a.get("sha256") != b.get("sha256"):
                    files.append({"path": path, "live_sha256": (a or {}).get("sha256"),
                                  "git_sha256": (b or {}).get("sha256")})
        name = _name((lrow or grow or brow).get("name"))
        rows.append({"id": "grok-workflow:" + directory, "dir_name": directory,
                     "kind": "numeric" if re.fullmatch(r"skill-\d{10,}", directory) else "named",
                     "name": name, "name_key": name.casefold() if name else None,
                     "state": state, "reason_codes": reasons, "join_basis": basis,
                     "duplicate_name": duplicate,
                     "name_changed": bool(lrow and grow and _name(lrow.get("name")) != _name(grow.get("name"))),
                     "export_state": export_state, "export_side": export_side,
                     "live_tree_sha256": lh, "git_tree_sha256": gh, "board_tree_sha256": bh,
                     "file_differences": files, "snapshot_collected_at": collected_at})
    return rows


def reconcile_instructions(git, board, agents, bots, *, observed_at, freshness=None):
    """Use the canonical matrix unchanged, with pre-read Git rows (no I/O)."""
    return _query.matrix(None, board, agents, bots, observed_at=observed_at,
                         freshness=freshness, git_rows=git)


def reconcile(snapshot, git, board=None, *, now, max_age=36 * 3600):
    """Live/Git report; partial disk inventories cannot prove missing_on_box."""
    live = {"state": snapshot["sources"]["box_disk"], "items": snapshot["domains"]["workflows"]}
    rows = reconcile_workflows(live, git, board, collected_at=snapshot.get("collected_at"),
                               now=now, max_age=max_age)
    return {"schema": "box-config-reconcile/v1", "domains": {"workflows": rows},
            "sources": {"live": live["state"], "git": git["state"],
                        "board": "not_visible_on_box" if board is None else board.get("state", "ok")},
            "summary": dict(Counter(row["state"] for row in rows))}
