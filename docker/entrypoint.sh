#!/usr/bin/env bash
set -e

# Default configuration from environment variables or k8s ConfigMap
MODEL_PATH="${MODEL_PATH:-/data/models/Qwen--Qwen2.5-1.5B-Instruct}"
SERVED_MODEL_NAME="${SERVED_MODEL_NAME:-Qwen2.5-1.5B-Instruct}"
PORT="${PORT:-8000}"
HOST="${HOST:-0.0.0.0}"
MAX_MODEL_LEN="${MAX_MODEL_LEN:-2048}"
BLOCK_SIZE="${BLOCK_SIZE:-32}"

# CPU Optimization: Limit OpenMP threads so k8s control-plane & system remain responsive
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-4}"

# Base vLLM arguments
ARGS=(
    serve
    "$MODEL_PATH"
    --served-model-name "$SERVED_MODEL_NAME"
    --host "$HOST"
    --port "$PORT"
    --max-model-len "$MAX_MODEL_LEN"
    --block-size "$BLOCK_SIZE"
    --enforce-eager
    --logging-config '{"pylogging_config_file": "/app/logging_config.json"}'
    --disable-log-outputs
)

# Enforce Bearer token authentication if API_KEY is passed from k8s Secret
if [ -n "$API_KEY" ]; then
    echo "[Entrypoint] Bearer authentication enabled (API key configured)."
    ARGS+=(--api-key "$API_KEY")
fi

# KV Cache memory limit if specified
if [ -n "$KV_CACHE_MEMORY_BYTES" ]; then
    echo "[Entrypoint] Enforcing KV Cache memory bytes: $KV_CACHE_MEMORY_BYTES"
    ARGS+=(--kv-cache-memory-bytes "$KV_CACHE_MEMORY_BYTES")
fi

# Append any additional flags passed to container
if [ "$#" -gt 0 ]; then
    ARGS+=("$@")
fi

echo "=================================================="
echo "[vLLM Server] Starting inference engine on CPU"
echo "[vLLM Server] Process UID: $(id -u), GID: $(id -g)"
echo "[vLLM Server] Model path: $MODEL_PATH"
echo "[vLLM Server] Logging config: /app/logging_config.json (PrivacyFilter enabled)"
echo "=================================================="

exec vllm "${ARGS[@]}"
