import json
import os
import statistics
import time
from typing import Any

from openai import OpenAI


BASE_URL = os.environ.get("CACHE_PROBE_BASE_URL", "https://knostarrouter.com/v1")
API_KEY = os.environ.get("CACHE_PROBE_API_KEY", "")
MODELS = [m for m in os.environ.get("CACHE_PROBE_MODELS", "claude-sonnet-4-6").split(",") if m]
TARGET_SUCCESSES = int(os.environ.get("CACHE_PROBE_TARGET_SUCCESSES", "12"))
MAX_ATTEMPTS = int(os.environ.get("CACHE_PROBE_MAX_ATTEMPTS", "24"))
RETRY_SLEEP_SECONDS = float(os.environ.get("CACHE_PROBE_RETRY_SLEEP_SECONDS", "5"))
TIMEOUT_SECONDS = float(os.environ.get("CACHE_PROBE_TIMEOUT_SECONDS", "240"))


def build_long_prefix() -> str:
    block = (
        "You are a deterministic cache probe. "
        "Return exactly the word OK and nothing else. "
        "This prefix must remain identical across repeated requests. "
        "It is intentionally verbose to cross the prompt-caching threshold. "
    )
    return "\n".join(f"{i:04d}: {block}" for i in range(220))


def extract_usage(payload: Any) -> dict[str, Any]:
    usage = getattr(payload, "usage", None)
    if usage is None and isinstance(payload, dict):
        usage = payload.get("usage")
    if usage is None:
        return {}
    if hasattr(usage, "model_dump"):
        return usage.model_dump()
    if isinstance(usage, dict):
        return usage
    return json.loads(json.dumps(usage, default=lambda o: getattr(o, "__dict__", str(o))))


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    successes = [r for r in records if r["status"] == "ok"]
    rate_limits = [r for r in records if r.get("error_type") == "RateLimitError"]
    cached_hits = [r for r in successes if (r.get("cached_tokens") or 0) > 0]
    elapsed = [r["elapsed_seconds"] for r in successes]
    cached_values = [r.get("cached_tokens") or 0 for r in successes]
    return {
        "attempts": len(records),
        "successes": len(successes),
        "rate_limit_errors": len(rate_limits),
        "other_errors": len([r for r in records if r["status"] == "error" and r.get("error_type") != "RateLimitError"]),
        "cached_hit_successes": len(cached_hits),
        "cached_hit_rate_among_successes": round(len(cached_hits) / len(successes), 3) if successes else None,
        "cached_tokens_unique_values": sorted(set(cached_values)) if successes else [],
        "elapsed_seconds_min": min(elapsed) if elapsed else None,
        "elapsed_seconds_median": round(statistics.median(elapsed), 3) if elapsed else None,
        "elapsed_seconds_max": max(elapsed) if elapsed else None,
    }


def main() -> None:
    if not API_KEY:
        raise SystemExit("CACHE_PROBE_API_KEY is required")
    client = OpenAI(base_url=BASE_URL, api_key=API_KEY, timeout=TIMEOUT_SECONDS)
    system_prompt = build_long_prefix()
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "Return exactly: OK"},
    ]

    overall: dict[str, Any] = {}

    for model in MODELS:
        print(f"=== MODEL {model} ===", flush=True)
        records: list[dict[str, Any]] = []
        successes = 0
        attempt = 0

        while successes < TARGET_SUCCESSES and attempt < MAX_ATTEMPTS:
            attempt += 1
            started = time.time()
            result: dict[str, Any] = {"model": model, "attempt": attempt}
            try:
                response = client.chat.completions.with_raw_response.create(
                    model=model,
                    messages=messages,
                    temperature=0,
                )
                elapsed = time.time() - started
                parsed = response.parse()
                body = parsed.model_dump()
                usage = extract_usage(parsed)
                prompt_details = usage.get("prompt_tokens_details") or {}
                result.update(
                    {
                        "status": "ok",
                        "success_index": successes + 1,
                        "elapsed_seconds": round(elapsed, 3),
                        "http_status": response.status_code,
                        "processing_ms_header": response.headers.get("openai-processing-ms"),
                        "request_id_header": response.headers.get("x-request-id"),
                        "usage": usage,
                        "cached_tokens": prompt_details.get("cached_tokens"),
                        "prompt_tokens": usage.get("prompt_tokens"),
                        "completion_tokens": usage.get("completion_tokens"),
                        "response_preview": body.get("choices", [{}])[0].get("message", {}).get("content"),
                    }
                )
                successes += 1
            except Exception as exc:
                elapsed = time.time() - started
                result.update(
                    {
                        "status": "error",
                        "elapsed_seconds": round(elapsed, 3),
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    }
                )
                if type(exc).__name__ == "RateLimitError":
                    time.sleep(RETRY_SLEEP_SECONDS)

            records.append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)

        overall[model] = {
            "summary": summarize(records),
            "records": records,
        }

    print("=== JSON_REPORT ===")
    print(json.dumps(overall, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
