import json
import os
import statistics
import time
from typing import Any

from openai import OpenAI


BASE_URL = os.environ.get("CACHE_PROBE_BASE_URL", "https://knostarrouter.com/v1")
API_KEY = os.environ.get("CACHE_PROBE_API_KEY", "")
MODEL = os.environ.get("CACHE_PROBE_MODEL", "claude-sonnet-4-6")
TARGET_SUCCESSES = int(os.environ.get("CACHE_PROBE_TARGET_SUCCESSES", "12"))
MAX_ATTEMPTS = int(os.environ.get("CACHE_PROBE_MAX_ATTEMPTS", "24"))
RETRY_SLEEP_SECONDS = float(os.environ.get("CACHE_PROBE_RETRY_SLEEP_SECONDS", "5"))
TIMEOUT_SECONDS = float(os.environ.get("CACHE_PROBE_TIMEOUT_SECONDS", "240"))
SUCCESS_SLEEP_SECONDS = float(os.environ.get("CACHE_PROBE_SUCCESS_SLEEP_SECONDS", "0"))


def build_long_text() -> str:
    block = (
        "This is a deterministic Anthropic cache probe. "
        "The content must remain identical across requests. "
        "Return exactly OK. "
        "This prefix is intentionally long enough to make prompt caching meaningful. "
    )
    return "\n".join(f"{i:04d}: {block}" for i in range(220))


def to_jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, (dict, list, str, int, float, bool)) or value is None:
        return value
    return json.loads(json.dumps(value, default=lambda o: getattr(o, "__dict__", str(o))))


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    successes = [r for r in records if r["status"] == "ok"]
    rate_limits = [r for r in records if r.get("error_type") == "RateLimitError"]
    usage_list = [r.get("usage", {}) for r in successes]
    cached_hits = [
        r for r in successes if ((r.get("usage", {}).get("prompt_tokens_details") or {}).get("cached_tokens") or 0) > 0
    ]
    elapsed = [r["elapsed_seconds"] for r in successes]
    creation_5m = sorted(set((u.get("claude_cache_creation_5_m_tokens") or 0) for u in usage_list))
    creation_1h = sorted(set((u.get("claude_cache_creation_1_h_tokens") or 0) for u in usage_list))
    cached_values = sorted(
        set((((u.get("prompt_tokens_details") or {}).get("cached_tokens")) or 0) for u in usage_list)
    )
    return {
        "attempts": len(records),
        "successes": len(successes),
        "rate_limit_errors": len(rate_limits),
        "other_errors": len([r for r in records if r["status"] == "error" and r.get("error_type") != "RateLimitError"]),
        "cached_hit_successes": len(cached_hits),
        "cached_hit_rate_among_successes": round(len(cached_hits) / len(successes), 3) if successes else None,
        "cached_tokens_unique_values": cached_values,
        "claude_cache_creation_5_m_tokens_unique_values": creation_5m,
        "claude_cache_creation_1_h_tokens_unique_values": creation_1h,
        "elapsed_seconds_min": min(elapsed) if elapsed else None,
        "elapsed_seconds_median": round(statistics.median(elapsed), 3) if elapsed else None,
        "elapsed_seconds_max": max(elapsed) if elapsed else None,
    }


def main() -> None:
    if not API_KEY:
        raise SystemExit("CACHE_PROBE_API_KEY is required")
    client = OpenAI(base_url=BASE_URL, api_key=API_KEY, timeout=TIMEOUT_SECONDS)
    long_text = build_long_text()
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": long_text,
                    "cache_control": {"type": "ephemeral"},
                },
                {
                    "type": "text",
                    "text": "Return exactly OK",
                },
            ],
        }
    ]

    records: list[dict[str, Any]] = []
    successes = 0
    attempt = 0

    while successes < TARGET_SUCCESSES and attempt < MAX_ATTEMPTS:
        attempt += 1
        started = time.time()
        item = {"model": MODEL, "attempt": attempt}
        try:
            response = client.chat.completions.with_raw_response.create(
                model=MODEL,
                messages=messages,
                temperature=0,
                extra_body={
                    "anthropic-beta": ["prompt-caching-2024-07-31"],
                },
            )
            parsed = response.parse()
            elapsed = time.time() - started
            usage = to_jsonable(getattr(parsed, "usage", None))
            body = parsed.model_dump()
            item.update(
                {
                    "status": "ok",
                    "success_index": successes + 1,
                    "elapsed_seconds": round(elapsed, 3),
                    "http_status": response.status_code,
                    "response_preview": body.get("choices", [{}])[0].get("message", {}).get("content"),
                    "usage": usage,
                }
            )
            successes += 1
        except Exception as exc:
            elapsed = time.time() - started
            item.update(
                {
                    "status": "error",
                    "elapsed_seconds": round(elapsed, 3),
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )
            if type(exc).__name__ == "RateLimitError":
                time.sleep(RETRY_SLEEP_SECONDS)

        records.append(item)
        print(json.dumps(item, ensure_ascii=False), flush=True)
        if item["status"] == "ok" and successes < TARGET_SUCCESSES and SUCCESS_SLEEP_SECONDS > 0:
            time.sleep(SUCCESS_SLEEP_SECONDS)

    report = {
        "summary": summarize(records),
        "records": records,
    }
    print("=== JSON_REPORT ===")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
