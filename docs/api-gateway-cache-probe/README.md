# API Gateway Cache Probe

This directory collects the durable notes for testing prompt caching or KV cache
behavior on third-party AI gateways.

It is intentionally split by interface style because the observable signals are
different:

- OpenAI-compatible API:
  - check `usage.prompt_tokens_details.cached_tokens`
  - endpoint usually looks like `https://host/v1`
- Anthropic-compatible API:
  - check `cache_creation_input_tokens` and `cache_read_input_tokens`
  - endpoint shape follows `/v1/messages`
- Claude Code gateway:
  - set `ANTHROPIC_BASE_URL` to the gateway root
  - Claude Code itself will call Anthropic-style endpoints such as
    `/v1/messages` and `/v1/messages/count_tokens`
  - local debug logs are often the only user-visible evidence when the gateway
    does not expose server-side request logs

## Scripts

Scripts live in
[`../../scripts/api-gateway-cache-probe`](../../scripts/api-gateway-cache-probe).

- [openai_compat_cache_probe.py](../../scripts/api-gateway-cache-probe/openai_compat_cache_probe.py)
  tests OpenAI-compatible chat completions and summarizes `cached_tokens`
- [anthropic_compat_cache_probe.py](../../scripts/api-gateway-cache-probe/anthropic_compat_cache_probe.py)
  tests Anthropic-style caching hints over an OpenAI-compatible client wrapper
- [compare_knostar_base_urls.py](../../scripts/api-gateway-cache-probe/compare_knostar_base_urls.py)
  compares a root gateway URL and a `/v1` API URL to catch documentation or
  routing mismatches
- [claude_code_gateway_cache_probe.sh](../../scripts/api-gateway-cache-probe/claude_code_gateway_cache_probe.sh)
  runs Claude Code itself against a gateway and records both JSON results and
  per-attempt debug logs

## Environment Variables

Python probes use:

- `CACHE_PROBE_API_KEY`
- `CACHE_PROBE_BASE_URL`
- `CACHE_PROBE_TARGET_SUCCESSES`
- `CACHE_PROBE_MAX_ATTEMPTS`
- `CACHE_PROBE_RETRY_SLEEP_SECONDS`
- `CACHE_PROBE_TIMEOUT_SECONDS`

Additional variables:

- `anthropic_compat_cache_probe.py`
  - `CACHE_PROBE_MODEL`
  - `CACHE_PROBE_SUCCESS_SLEEP_SECONDS`
- `openai_compat_cache_probe.py`
  - `CACHE_PROBE_MODELS`
- `compare_knostar_base_urls.py`
  - `CACHE_PROBE_BASE_URL_ROOT`

Claude Code probe uses:

- `ATTEMPTS`
- `SLEEP_SECONDS`
- `MODEL`
- `RUN_DIR`

The Claude Code probe expects Claude Code configuration to point at the gateway
under test before execution. Restore the previous `~/.claude/settings.json`
after the run if you changed it for the probe.

## Current Knostar Notes

The current working notes for `knostarrouter.com` are in
[knostarrouter-findings.md](knostarrouter-findings.md).

Known local run artifacts are kept outside the repo. The Claude Code run from
2026-05-13 is in
`/private/tmp/claude-code-cache-probe-20260513-150334`.
