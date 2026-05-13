#!/usr/bin/env bash
set -euo pipefail

ATTEMPTS="${ATTEMPTS:-4}"
SLEEP_SECONDS="${SLEEP_SECONDS:-75}"
MODEL="${MODEL:-claude-sonnet-4-6}"
RUN_DIR="${RUN_DIR:-/private/tmp/claude-code-cache-probe-$(date +%Y%m%d-%H%M%S)}"

mkdir -p "$RUN_DIR"

APPEND_PROMPT_FILE="$RUN_DIR/append-system-prompt.txt"
{
  echo "Deterministic cache probe for Claude Code through an Anthropic-compatible gateway."
  echo "Return exactly the word OK and nothing else."
  echo "The following repeated prefix is intentionally long so prompt caching should be eligible."
  for i in $(seq 1 260); do
    printf '%04d: Static prefix for cache validation. Keep this exact content unchanged across attempts.\n' "$i"
  done
} > "$APPEND_PROMPT_FILE"

echo "run_dir=$RUN_DIR"

for i in $(seq 1 "$ATTEMPTS"); do
  json_file="$RUN_DIR/attempt-$i.json"
  debug_file="$RUN_DIR/attempt-$i.debug.log"

  claude \
    -p "Return exactly OK" \
    --model "$MODEL" \
    --output-format json \
    --no-session-persistence \
    --dangerously-skip-permissions \
    --debug-file "$debug_file" \
    --append-system-prompt "$(cat "$APPEND_PROMPT_FILE")" \
    > "$json_file"

  jq -c '{
    attempt: '"$i"',
    is_error,
    duration_ms,
    model: .modelUsage | keys[0],
    input_tokens: .usage.input_tokens,
    cache_creation_input_tokens: .usage.cache_creation_input_tokens,
    cache_read_input_tokens: .usage.cache_read_input_tokens,
    cache_creation: .usage.cache_creation,
    output_tokens: .usage.output_tokens,
    stop_reason
  }' "$json_file"

  if [ "$i" -lt "$ATTEMPTS" ]; then
    sleep "$SLEEP_SECONDS"
  fi
done

jq -s '{
  attempts: length,
  errors: map(select(.is_error == true)) | length,
  successes: map(select(.is_error != true)) | length,
  cache_creation_positive: map(select((.usage.cache_creation_input_tokens // 0) > 0)) | length,
  cache_read_positive: map(select((.usage.cache_read_input_tokens // 0) > 0)) | length,
  cache_creation_values: [.[].usage.cache_creation_input_tokens] | unique,
  cache_read_values: [.[].usage.cache_read_input_tokens] | unique,
  durations_ms: [.[].duration_ms],
  files: [range(0; length) | "attempt-\(.+1).json"]
}' "$RUN_DIR"/attempt-*.json | tee "$RUN_DIR/summary.json"
