# Knostarrouter Findings

Date of latest update: 2026-05-13

## Scope

Gateway under test:

- Claude Code gateway root: `https://knostarrouter.com`
- OpenAI-compatible API base: `https://knostarrouter.com/v1`

Models tested:

- `claude-sonnet-4-6`
- `gpt-5.4` was attempted once for availability and failed with
  `503 model_not_found` / no available channel

## Confirmed Findings

### 1. OpenAI-compatible `/v1` endpoint works for `claude-sonnet-4-6`

Using the OpenAI Python client against `https://knostarrouter.com/v1`, the
gateway returns normal `chat.completion` JSON and usage objects.

### 2. OpenAI-compatible cache signal is not stable

Across repeated tests, `usage.prompt_tokens_details.cached_tokens` sometimes
stays at `0` and sometimes jumps to `23853`.

Important nuance:

- the fixed large value itself is not automatically suspicious
- if the cached prefix is identical, the hit size can naturally be constant
- the suspicious part is the instability: equivalent requests alternated
  between `0` and `23853`

### 3. Claude Code root URL is not the same thing as `/v1`

Direct comparison showed:

- `https://knostarrouter.com` returns the site HTML when used as an OpenAI
  client base URL
- `https://knostarrouter.com/v1` returns the actual OpenAI-compatible API JSON

So the root URL should not be reused as an OpenAI SDK base URL.

### 4. Claude Code gateway compatibility is incomplete

When Claude Code itself was pointed at `https://knostarrouter.com`, the main
model request could still succeed, but the local debug logs showed:

- `404 Invalid URL (POST /v1/messages/count_tokens)`

This means the gateway does not currently implement the full Anthropic /
Claude Code endpoint set that Claude Code expects.

### 5. Claude Code-visible Anthropic cache usage stayed at zero

In a low-frequency serial run through Claude Code itself, all four attempts
returned:

- `cache_creation_input_tokens = 0`
- `cache_read_input_tokens = 0`
- `cache_creation.ephemeral_1h_input_tokens = 0`
- `cache_creation.ephemeral_5m_input_tokens = 0`

This does not prove the server has no caching at all, but it does show that the
official Anthropic cache usage signals were not exposed to the client in that
run.

## Practical Interpretation

For user-visible validation, current evidence supports:

1. OpenAI-compatible cache reporting exists but is unstable.
2. Claude Code compatibility is incomplete because `count_tokens` is missing.
3. Claude Code-visible Anthropic cache usage is currently all zero.

## Reproduction Pointers

- OpenAI-compatible probe:
  [openai_compat_cache_probe.py](../../scripts/api-gateway-cache-probe/openai_compat_cache_probe.py)
- Anthropic-style probe over `/v1`:
  [anthropic_compat_cache_probe.py](../../scripts/api-gateway-cache-probe/anthropic_compat_cache_probe.py)
- Claude Code gateway probe:
  [claude_code_gateway_cache_probe.sh](../../scripts/api-gateway-cache-probe/claude_code_gateway_cache_probe.sh)
- Claude Code run artifacts:
  `/private/tmp/claude-code-cache-probe-20260513-150334`
