#!/usr/bin/env python3
"""
Thin Logseq HTTP transport.

This module is intentionally narrow:
- discover URL/token
- call the local HTTP API
- normalize response envelopes

It must not make ACNS policy decisions.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


class LogseqTransportError(Exception):
    """Base transport exception."""


class LogseqTransportAuthError(LogseqTransportError):
    """Authentication failed."""


class LogseqTransportConnectionError(LogseqTransportError):
    """Connection failed."""


def _load_token_from_logseq_app_config() -> str:
    config_path = Path.home() / "Library" / "Application Support" / "Logseq" / "configs.edn"
    if not config_path.exists():
        return ""

    text = config_path.read_text()
    match = re.search(r':name\s+"lsq"\s*,?\s*:value\s+"([^"]+)"', text)
    return match.group(1) if match else ""


@dataclass
class LogseqHTTPTransport:
    url: str = "http://127.0.0.1:12315"
    token: str = ""

    @classmethod
    def from_defaults(
        cls,
        url: Optional[str] = None,
        token: Optional[str] = None,
    ) -> "LogseqHTTPTransport":
        resolved_url = url or os.environ.get("LOGSEQ_API_URL", "http://127.0.0.1:12315")
        resolved_token = token or os.environ.get("LOGSEQ_API_TOKEN", "") or _load_token_from_logseq_app_config()
        return cls(url=resolved_url, token=resolved_token)

    def call(self, method: str, args: Optional[list[Any]] = None) -> Any:
        if not self.token:
            raise LogseqTransportAuthError("No Logseq API token is available.")

        request = urllib.request.Request(
            f"{self.url}/api",
            data=json.dumps({"method": method, "args": args or []}).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.token}",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode())
                return self._normalize_payload(payload)
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                raise LogseqTransportAuthError("Invalid Logseq API token.") from exc
            raise LogseqTransportConnectionError(f"HTTP error {exc.code}: {exc.reason}") from exc
        except urllib.error.URLError as exc:
            raise LogseqTransportConnectionError(f"Connection failed: {exc.reason}") from exc

    @staticmethod
    def _normalize_payload(payload: Any) -> Any:
        if isinstance(payload, dict):
            if "error" in payload:
                raise LogseqTransportError(str(payload["error"]))
            if "result" in payload:
                return payload["result"]
        return payload

    def get_current_graph(self) -> Any:
        return self.call("logseq.App.getCurrentGraph")

    def get_page(self, title: str) -> Any:
        return self.call("logseq.Editor.getPage", [title])

    def get_page_blocks_tree(self, title: str) -> Any:
        return self.call("logseq.Editor.getPageBlocksTree", [title])

    def create_page(self, title: str, properties: Optional[dict[str, Any]] = None, create_first_block: bool = True) -> Any:
        return self.call("logseq.Editor.createPage", [title, properties or {}, {"createFirstBlock": create_first_block}])

    def insert_block(
        self,
        parent_uuid: str,
        content: str,
        sibling: bool = False,
        properties: Optional[dict[str, Any]] = None,
    ) -> Any:
        options: dict[str, Any] = {"sibling": sibling}
        if properties:
            options["properties"] = properties
        return self.call("logseq.Editor.insertBlock", [parent_uuid, content, options])

    def update_block(self, uuid: str, content: str) -> Any:
        self.call("logseq.Editor.updateBlock", [uuid, content])
        return self.call("logseq.Editor.getBlock", [uuid, {"includeChildren": True}])

    def upsert_block_property(self, uuid: str, key: str, value: Any) -> Any:
        return self.call("logseq.Editor.upsertBlockProperty", [uuid, key, value])

    def probe_response_shapes(self) -> dict[str, str]:
        """
        Run a small read-only probe set against the local API.

        The result maps method names to the top-level payload shape:
        - bare-dict
        - bare-list
        - wrapped-result
        - scalar
        """

        probe_methods = {
            "logseq.App.getCurrentGraph": [],
            "logseq.Editor.getPage": ["Inbox"],
        }
        results: dict[str, str] = {}
        for method, args in probe_methods.items():
            raw = self._call_raw(method, args)
            results[method] = self._describe_payload_shape(raw)
        return results

    def _call_raw(self, method: str, args: Optional[list[Any]] = None) -> Any:
        """
        Execute a raw HTTP call without normalizing the response envelope.
        """

        if not self.token:
            raise LogseqTransportAuthError("No Logseq API token is available.")

        request = urllib.request.Request(
            f"{self.url}/api",
            data=json.dumps({"method": method, "args": args or []}).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.token}",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                raise LogseqTransportAuthError("Invalid Logseq API token.") from exc
            raise LogseqTransportConnectionError(f"HTTP error {exc.code}: {exc.reason}") from exc
        except urllib.error.URLError as exc:
            raise LogseqTransportConnectionError(f"Connection failed: {exc.reason}") from exc

    @staticmethod
    def _describe_payload_shape(payload: Any) -> str:
        if isinstance(payload, dict):
            if "result" in payload:
                return "wrapped-result"
            return "bare-dict"
        if isinstance(payload, list):
            return "bare-list"
        return "scalar"


def _main(argv: list[str]) -> int:
    transport = LogseqHTTPTransport.from_defaults()

    if len(argv) >= 2 and argv[1] == "probe":
        print(json.dumps(transport.probe_response_shapes(), indent=2, ensure_ascii=False))
        return 0

    print("Usage: python logseq_http.py probe")
    return 1


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
