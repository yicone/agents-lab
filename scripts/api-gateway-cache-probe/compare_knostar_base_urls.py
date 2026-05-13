import json
import os
import time

from openai import OpenAI


API_KEY = os.environ.get("CACHE_PROBE_API_KEY", "")
BASE_URLS = [
    os.environ.get("CACHE_PROBE_BASE_URL_ROOT", "https://knostarrouter.com"),
    os.environ.get("CACHE_PROBE_BASE_URL", "https://knostarrouter.com/v1"),
]


def to_jsonable(value):
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, (dict, list, str, int, float, bool)):
        return value
    return json.loads(json.dumps(value, default=lambda o: getattr(o, "__dict__", str(o))))


def main() -> None:
    if not API_KEY:
        raise SystemExit("CACHE_PROBE_API_KEY is required")
    messages = [{"role": "user", "content": "Return exactly OK"}]
    for base_url in BASE_URLS:
        started = time.time()
        item = {"base_url": base_url}
        try:
            client = OpenAI(base_url=base_url, api_key=API_KEY, timeout=120.0)
            response = client.chat.completions.with_raw_response.create(
                model="claude-sonnet-4-6",
                messages=messages,
                temperature=0,
            )
            parsed = response.parse()
            usage = to_jsonable(getattr(parsed, "usage", None))
            body = to_jsonable(parsed)
            item.update(
                {
                    "status": "ok",
                    "elapsed_seconds": round(time.time() - started, 3),
                    "http_status": response.status_code,
                    "parsed_type": type(parsed).__name__,
                    "body_type": type(body).__name__,
                    "response_preview": (body.get("choices", [{}])[0].get("message", {}).get("content") if isinstance(body, dict) else str(body)[:200]),
                    "usage": usage,
                    "body": body,
                }
            )
        except Exception as exc:
            item.update(
                {
                    "status": "error",
                    "elapsed_seconds": round(time.time() - started, 3),
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )
        print(json.dumps(item, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
