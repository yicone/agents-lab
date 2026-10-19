"""Private plan files and GET-only views. HTTP reads never create or repair files."""
from __future__ import annotations

import base64
import hashlib
import os
import re
import stat
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .plan_contract import G1, G2, HASH_ALGO, canonical, exact, strict_json, utc, uuid, validate_payload
from .runtime_plan import DEFAULT_PLAN_DIR, MAX_BYTES
from .views import BadRequest, NotFound

NAME = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.json$")


class PlanError(ValueError):
    pass


def _private(st, mode, directory=False):
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if (not kind(st.st_mode) or st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) != mode
            or (not directory and st.st_nlink != 1)):
        raise PlanError("permissions")


def _directory(path, *, create=False):
    # Open every component without following symlinks, not just the final path.
    absolute = Path(os.path.abspath(path))
    fd = os.open(absolute.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in absolute.parts[1:]:
            try:
                child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(component, mode=0o700, dir_fd=fd)
                except FileExistsError:
                    pass
                child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        _private(os.fstat(fd), 0o700, directory=True)
        return fd
    except Exception:
        os.close(fd)
        raise


def _read(fd, name):
    try:
        handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(handle, "rb") as stream:
            before = os.fstat(stream.fileno())
            _private(before, 0o600)
            if before.st_size > MAX_BYTES:
                raise PlanError("too_large")
            raw = stream.read(MAX_BYTES + 1)
            after = os.fstat(stream.fileno())
            if len(raw) > MAX_BYTES:
                raise PlanError("too_large")
            if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise PlanError("read_error")
    except PlanError:
        raise
    except OSError as exc:
        raise PlanError("permissions" if exc.errno in {1, 13, 40, 62} else "read_error") from None
    try:
        plan = strict_json(raw)
    except (ValueError, UnicodeError, RecursionError):
        raise PlanError("invalid_json") from None
    try:
        exact(plan, ("payload", "hash_algo", "plan_hash"))
        if plan["hash_algo"] != HASH_ALGO or not isinstance(plan["plan_hash"], str) or not re.fullmatch(r"[0-9a-f]{64}", plan["plan_hash"]):
            raise ValueError("invalid hash envelope")
        validate_payload(plan["payload"])
        if plan["payload"]["plan_id"] + ".json" != name:
            raise ValueError("file identity mismatch")
        encoded = canonical(plan["payload"])
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        raise PlanError("invalid_schema") from None
    return plan, encoded, hashlib.sha256(encoded).hexdigest() == plan["plan_hash"]


def write_plan(directory, plan):
    validate_payload(plan["payload"])
    raw = canonical(plan)
    if len(raw) > MAX_BYTES:
        raise PlanError("too_large")
    # Create only new directories; do not chmod user-owned existing paths.
    fd = _directory(directory, create=True)
    temp = "." + str(uuid4()) + ".tmp"
    try:
        handle = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        with os.fdopen(handle, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        # link is atomic and never replaces an existing plan.
        os.link(temp, plan["payload"]["plan_id"] + ".json", src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
        os.unlink(temp, dir_fd=fd)
        os.fsync(fd)
    finally:
        try:
            os.unlink(temp, dir_fd=fd)
        except FileNotFoundError:
            pass
        os.close(fd)


class Plans:
    def __init__(self, directory=DEFAULT_PLAN_DIR):
        self.directory = Path(directory)

    def list(self):
        response = {"generated_at": None, "items": [], "complete": True, "directory_error": None}
        try:
            fd = _directory(self.directory)
        except FileNotFoundError:
            return response
        except (OSError, PlanError):
            response.update(complete=False, directory_error="permissions")
            return response
        try:
            # Include malformed/schema-unknown UUID files, but never expose arbitrary filenames.
            names = sorted(n for n in os.listdir(fd) if NAME.fullmatch(n))
            if len(names) > 1000:
                response.update(complete=False, directory_error="directory_limit")
                names = names[:1000]
            for name in names:
                item = {k: None for k in ("generated_at", "expires_at", "mode", "schema_version", "server_identity", "snapshot_kind", "observed_at",
                                         "workspace_allow_list", "counts", "agent_count", "plan_hash", "expired")}
                item.update(plan_id=name[:-5], hash_verified=False, parse_error=None)
                try:
                    plan, _, verified = _read(fd, name)
                    payload = plan["payload"]
                    for k in ("generated_at", "expires_at", "mode", "schema_version", "server_identity", "snapshot_kind", "workspace_allow_list", "counts"):
                        item[k] = payload[k]
                    item["observed_at"] = payload["rows"][0]["observed_at"]
                    item.update(agent_count=len(payload["rows"]), plan_hash=plan["plan_hash"], hash_verified=verified,
                                expired=datetime.now(timezone.utc) >= utc(payload["expires_at"]))
                except PlanError as exc:
                    item["parse_error"] = str(exc)
                response["items"].append(item)
        except OSError:
            response.update(complete=False, directory_error="read_error")
        finally:
            os.close(fd)
        response["items"].sort(key=lambda x: (x["generated_at"] or "", x["plan_id"]), reverse=True)
        return response

    def detail(self, plan_id):
        try:
            uuid(plan_id)
        except ValueError:
            raise BadRequest("invalid plan UUID") from None
        try:
            fd = _directory(self.directory)
        except FileNotFoundError:
            raise NotFound("plan not found") from None
        except (OSError, PlanError):
            raise PlanError("permissions") from None
        try:
            try:
                os.stat(plan_id + ".json", dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                raise NotFound("plan not found") from None
            plan, encoded, verified = _read(fd, plan_id + ".json")
        finally:
            os.close(fd)
        rows = plan["payload"]["rows"]
        g1 = {}
        for k in G1:
            states = [r["checks"][k]["status"] for r in rows]
            g1[k] = "FAIL" if "FAIL" in states else "PASS" if all(s == "PASS" for s in states) else "UNKNOWN"
        return {"generated_at": None, "plan": plan, "hash_verified": verified, "hash_algo": HASH_ALGO,
                "canonical_payload_b64": base64.b64encode(encoded).decode("ascii"),
                "gates": {"g1": g1, "g2": {k: "absent" for k in G2}}, "apply_enabled": False,
                "expired": datetime.now(timezone.utc) >= utc(plan["payload"]["expires_at"])}
