#!/usr/bin/env python3
"""Conservative CodexBar account rotation; dry-run unless --apply is explicit."""
import argparse
import datetime as dt
import fcntl
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys

UTC = dt.timezone.utc


class Unavailable(Exception):
    pass


def command(argv, codes=(0,), timeout=90):
    # Never relay command stdout/stderr on failure: upstream errors may contain secrets.
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        raise Unavailable("command_unavailable") from None
    if result.returncode not in codes:
        raise Unavailable("command_failed")
    return result.stdout


def read_json(argv, codes=(0,)):
    try:
        return json.loads(command(argv, codes))
    except (ValueError, TypeError):
        raise Unavailable("invalid_json") from None


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def instant(value):
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else None
    except (AttributeError, TypeError, ValueError):
        return None


def prefix(email):
    return re.sub(r"[^a-zA-Z0-9_.+-]", "_", email.split("@", 1)[0])[:64]


def inventory(rows):
    if not isinstance(rows, list) or not rows:
        raise Unavailable("invalid_inventory")
    for row in rows:
        if (not isinstance(row, dict) or not isinstance(row.get("email"), str)
                or "@" not in row["email"] or not isinstance(row.get("id"), str)
                or not isinstance(row.get("isSystemAccount"), bool)):
            raise Unavailable("invalid_inventory")
    if len({r["id"] for r in rows}) != len(rows) or len({r["email"] for r in rows}) != len(rows):
        raise Unavailable("ambiguous_inventory")
    current = [r for r in rows if r["isSystemAccount"]]
    if len(current) != 1:
        raise Unavailable("unknown_current")
    return current[0]


def snapshot(accounts, payload, now):
    if not isinstance(payload, list):
        raise Unavailable("invalid_usage")
    views = []
    for account in accounts:
        email = account["email"]
        matches = [r for r in payload if isinstance(r, dict) and r.get("provider") == "codex"
                   and (r.get("usage", {}).get("accountEmail") == email
                        if isinstance(r.get("usage"), dict) else False)]
        view = dict(id=account["id"], email=email, account=prefix(email),
                    session=None, weekly=None, credits=None, kind="unavailable", reason="missing_or_error")
        views.append(view)
        if len(matches) != 1 or matches[0].get("error"):
            continue
        row = matches[0]
        usage = row["usage"]
        updated = instant(usage.get("updatedAt"))
        if updated is None or not 0 <= (now - updated).total_seconds() <= 300:
            view["reason"] = "stale_or_unknown_timestamp"
            continue
        if usage.get("dataConfidence") != "exact":
            view["reason"] = "inexact_usage"
            continue
        credit = row.get("credits") or {}
        if not isinstance(credit, dict):
            view["reason"] = "invalid_credits"
            continue
        if (credit.get("balanceReadSucceeded") is True and number(credit.get("remaining"))
                and credit["remaining"] > 0):
            view["credits"] = credit["remaining"]
        if usage.get("loginMethod") == "free":
            view.update(kind="credits" if view["credits"] else "unavailable", reason="free_credits_only")
            continue
        resets = []
        for key, label, minutes in (("primary", "session", 300), ("secondary", "weekly", 10080)):
            window = usage.get(key)
            if not isinstance(window, dict):
                break
            used = window.get("usedPercent")
            reset = instant(window.get("resetsAt"))
            if (not number(used) or not 0 <= used <= 100 or window.get("windowMinutes") != minutes
                    or reset is None or reset <= now):
                break
            view[label] = 100 - used
            resets.append((reset - now).total_seconds())
        else:
            view.update(kind="windows", reason="ok", headroom=min(view["session"], view["weekly"]),
                        reset_seconds=min(resets))
    return views


def choose(current_id, views, low=15, target=40, allow_credits=False):
    current = next(v for v in views if v["id"] == current_id)
    if current["kind"] != "windows":
        return None, "current_quota_unknown"  # No inferred exhaustion from missing data.
    if current["headroom"] >= low:
        return None, "current_above_threshold"
    candidates = [v for v in views if v["id"] != current_id and v["kind"] == "windows"
                  and v["headroom"] >= target]
    if candidates:
        # Reserve first: group bottleneck remaining into 10-point bands, then use the
        # nearer reset first within a band. Deterministic final tie break.
        return sorted(candidates, key=lambda v: (-(v["headroom"] // 10), v["reset_seconds"],
                                                 -v["headroom"], v["id"]))[0], "low_quota"
    if allow_credits:
        candidates = [v for v in views if v["id"] != current_id and v["kind"] == "credits"]
        if candidates:
            return sorted(candidates, key=lambda v: (-v["credits"], v["id"]))[0], "credits_fallback"
    return None, "no_eligible_target"


def idle(multica, profile=None):
    argv = [multica]
    if profile:
        argv += ["--profile", profile]
    status = read_json(argv + ["daemon", "status", "--output", "json"])
    if not isinstance(status, dict) or status.get("status") != "running":
        raise Unavailable("daemon_unknown")
    for key in ("active_task_count", "running_task_count", "resource_wait_task_count"):
        value = status.get(key)
        if type(value) is not int or value < 0:
            raise Unavailable("daemon_counts_unknown")
        if value:
            # The current CLI exposes only aggregate counts, not task runtime
            # identities. Never infer non-Codex ownership from absent processes.
            return False, "task_runtime_unknown"
    processes = command(["/bin/ps", "-axo", "comm="])
    if not processes.strip():
        raise Unavailable("process_list_unknown")
    for line in processes.splitlines():
        name = line.strip().rsplit("/", 1)[-1].lower()
        # Include CLI, app-server, desktop helpers and ACP wrappers; idle desktop
        # processes deliberately block too. ps comm avoids inspecting argv secrets.
        if name.startswith("codex") and not name.startswith("codexbar"):
            return False, "codex_process_present"
    return True, "idle"


def emit(record, log):
    line = json.dumps(record, ensure_ascii=True, separators=(",", ":"))
    if log:
        path = Path(log).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")
    print(line)


def run(args):
    record = dict(time=dt.datetime.now(UTC).isoformat(), mode="apply" if args.apply else "dry-run",
                  current=None, accounts=[], action="skip", reason="unknown")
    try:
        accounts = read_json([args.codexbar, "codex-accounts", "list", "--json"])
        current = inventory(accounts)
        record["current"] = prefix(current["email"])
        # Exit 1 may mean ONE account failed; retain individually validated entries.
        payload = read_json([args.codexbar, "usage", "--provider", "codex", "--all-accounts",
                             "--format", "json"], codes=(0, 1))
        views = snapshot(accounts, payload, dt.datetime.now(UTC))
        record["accounts"] = [{k: v[k] for k in ("account", "session", "weekly", "credits", "kind", "reason")}
                              for v in views]
        target, reason = choose(current["id"], views, args.low, args.target, args.allow_credits)
        record["reason"] = reason
        safe, busy_reason = idle(args.multica, args.multica_profile)
        if not safe:
            record["reason"] = busy_reason
        elif target:
            record["target"] = target["account"]
            record["action"] = "would_promote"
            if args.apply:
                # Recheck after the potentially slow usage fetch and immediately
                # before mutation. Abort if any account metadata has changed.
                fresh = read_json([args.codexbar, "codex-accounts", "list", "--json"])
                inventory(fresh)
                if sorted(fresh, key=lambda r: r["id"]) != sorted(accounts, key=lambda r: r["id"]):
                    raise Unavailable("inventory_changed")
                if not idle(args.multica, args.multica_profile)[0]:
                    raise Unavailable("became_busy")
                # The preflight commands can themselves take time. Do not promote
                # a candidate whose original reading has aged out in the meantime.
                rechecked = snapshot(accounts, payload, dt.datetime.now(UTC))
                still_target, _ = choose(current["id"], rechecked, args.low, args.target, args.allow_credits)
                if still_target is None or still_target["id"] != target["id"]:
                    raise Unavailable("quota_snapshot_expired")
                command([args.codexbar, "codex-accounts", "promote", target["id"], "--json"])
                record["action"] = "promoted"
    except Unavailable as error:
        record.update(action="skip", reason=str(error))
    except (KeyError, TypeError, ValueError):
        record.update(action="skip", reason="unsupported_schema")
    emit(record, args.log)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="default; no promote and no persistent log")
    mode.add_argument("--apply", action="store_true", help="explicitly allow promote when idle")
    parser.add_argument("--allow-credits", action="store_true", help="allow Free credits as last resort")
    parser.add_argument("--low", type=float, default=15)
    parser.add_argument("--target", type=float, default=40)
    parser.add_argument("--codexbar", default="/opt/homebrew/bin/codexbar")
    parser.add_argument("--multica", default=str(Path.home() / ".local/bin/multica"))
    parser.add_argument("--multica-profile", help="explicit host profile for launchd; omit inside a managed task")
    parser.add_argument("--log", help="optional JSONL log; stdout always contains exactly one decision")
    args = parser.parse_args()
    if not 0 <= args.low < args.target <= 100:
        parser.error("require 0 <= low < target <= 100")
    # Lock the script inode: no lockfile creation in dry-run, released on exit/crash.
    with open(__file__, "rb") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            emit(dict(time=dt.datetime.now(UTC).isoformat(), action="skip", reason="already_running"), args.log)
            return
        run(args)


if __name__ == "__main__":
    main()
