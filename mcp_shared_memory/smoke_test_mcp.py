#!/usr/bin/env python3
import json
import os
import subprocess
import sys
from typing import Any, Dict, Optional

sys.dont_write_bytecode = True


def _send(proc: subprocess.Popen[str], msg: Dict[str, Any]) -> None:
    proc.stdin.write(json.dumps(msg, separators=(",", ":")) + "\n")
    proc.stdin.flush()


def _recv(proc: subprocess.Popen[str]) -> Optional[Dict[str, Any]]:
    line = proc.stdout.readline()
    if not line:
        return None
    return json.loads(line)

def _frame(payload: Dict[str, Any]) -> bytes:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return f"Content-Length: {len(data)}\r\n\r\n".encode("ascii") + data


def _send_framed(proc: subprocess.Popen[bytes], msg: Dict[str, Any]) -> None:
    assert proc.stdin is not None
    proc.stdin.write(_frame(msg))
    proc.stdin.flush()


def _recv_framed(proc: subprocess.Popen[bytes]) -> Optional[Dict[str, Any]]:
    assert proc.stdout is not None
    headers: Dict[str, str] = {}
    line = proc.stdout.readline()
    if not line:
        return None
    while line in (b"\n", b"\r\n"):
        line = proc.stdout.readline()
        if not line:
            return None
    while line not in (b"\n", b"\r\n"):
        k, v = line.decode("utf-8").split(":", 1)
        headers[k.strip().lower()] = v.strip()
        line = proc.stdout.readline()
        if not line:
            return None
    n = int(headers["content-length"])
    body = proc.stdout.read(n)
    if not body:
        return None
    return json.loads(body.decode("utf-8"))


def main() -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    server_py = os.path.join(here, "server.py")
    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    env.setdefault("MCP_MEMORY_DB_PATH", os.path.expanduser("~/.codex/mcp/shared-memory/memory.sqlite3"))

    proc = subprocess.Popen(
        ["python3", server_py],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    assert proc.stdin and proc.stdout

    try:
        # JSONL path (some clients)
        _send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {"elicitation": {}}, "clientInfo": {"name": "smoke"}},
            },
        )
        r1 = _recv(proc)
        if not r1 or r1.get("id") != 1:
            raise RuntimeError(f"bad initialize response (jsonl): {r1!r}")
        _send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        _send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        r2 = _recv(proc)
        if not r2 or r2.get("id") != 2:
            raise RuntimeError(f"bad tools/list response (jsonl): {r2!r}")
        _send(
            proc,
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "memory_stats", "arguments": {}}},
        )
        r3 = _recv(proc)
        if not r3 or r3.get("id") != 3:
            raise RuntimeError(f"bad tools/call response (jsonl): {r3!r}")

        proc.terminate()
        proc.wait(timeout=5)

        # Content-Length path (some clients)
        proc2 = subprocess.Popen(
            ["python3", server_py],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        try:
            _send_framed(
                proc2,
                {
                    "jsonrpc": "2.0",
                    "id": 11,
                    "method": "initialize",
                    "params": {"protocolVersion": "2025-06-18", "capabilities": {"elicitation": {}}, "clientInfo": {"name": "smoke"}},
                },
            )
            r11 = _recv_framed(proc2)
            if not r11 or r11.get("id") != 11:
                raise RuntimeError(f"bad initialize response (framed): {r11!r}")
            _send_framed(proc2, {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
            _send_framed(proc2, {"jsonrpc": "2.0", "id": 12, "method": "tools/list", "params": {}})
            r12 = _recv_framed(proc2)
            if not r12 or r12.get("id") != 12:
                raise RuntimeError(f"bad tools/list response (framed): {r12!r}")
            _send_framed(
                proc2,
                {"jsonrpc": "2.0", "id": 13, "method": "tools/call", "params": {"name": "memory_stats", "arguments": {}}},
            )
            r13 = _recv_framed(proc2)
            if not r13 or r13.get("id") != 13:
                raise RuntimeError(f"bad tools/call response (framed): {r13!r}")
        finally:
            proc2.terminate()
            try:
                proc2.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc2.kill()

        print("ok")
        return 0
    except Exception as e:
        try:
            err = proc.stderr.read()
        except Exception:
            err = ""
        sys.stderr.write(f"smoke_test_failed: {e}\n")
        if err:
            sys.stderr.write(err + "\n")
        return 2
    finally:
        try:
            proc.terminate()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
