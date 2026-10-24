"""Conservative recognition of upstream errors disguised as successful output.

Only final result.output is inspected. Never persist arbitrary assistant text.
"""
from __future__ import annotations

import re
from typing import Any

from .redact import redact

_GENERIC = re.compile(
    r"\[Error\] An error occurred while processing your request\."
    r"(?: You can retry your request, or contact us through our help center at "
    r"help\.openai\.com if the error persists\.)?"
    r"(?: Please include the request ID "
    r"(?P<request_id>[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})"
    r" in your message\.)?"
)


def delivery_anomaly(task: dict[str, Any]) -> dict[str, Any]:
    empty = {"delivery_anomaly": None, "request_id": None, "model": None}
    if task.get("status") != "completed":
        return empty
    result = task.get("result")
    output = result.get("output") if isinstance(result, dict) else None
    if not isinstance(output, str):
        return empty
    match = _GENERIC.fullmatch(output.strip())
    if not match:
        return empty
    # Usage is run-specific; never substitute the agent's current configured model.
    usage = task.get("usage")
    models = set()
    for item in usage if isinstance(usage, list) else []:
        model = item.get("model") if isinstance(item, dict) else None
        if (isinstance(model, str) and re.fullmatch(r"[A-Za-z0-9_.:/+-]{1,160}", model)
                and redact(model, limit=None) == model):
            models.add(model)
    return {"delivery_anomaly": "upstream_error", "request_id": match.group("request_id"),
            "model": next(iter(models)) if len(models) == 1 else None}
