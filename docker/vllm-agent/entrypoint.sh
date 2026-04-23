#!/usr/bin/env bash
# Runs inside the rented vast.ai box. Starts vLLM + the sc-agent in parallel.
# If either process dies, the container exits so vast.ai restarts it.
set -Eeuo pipefail

: "${SC_MODEL_SLUG:?SC_MODEL_SLUG is required}"
: "${SC_CONTROL_URL:?SC_CONTROL_URL is required}"
: "${SC_AGENT_SECRET:?SC_AGENT_SECRET is required}"
: "${SC_INSTANCE_ID:?SC_INSTANCE_ID is required}"

VLLM_ARGS="${VLLM_ARGS:-}"

echo "[entrypoint] starting vLLM with: ${VLLM_ARGS}"
python -m vllm.entrypoints.openai.api_server ${VLLM_ARGS} &
VLLM_PID=$!

echo "[entrypoint] starting sc-agent"
python -m agent.main &
AGENT_PID=$!

cleanup() {
  echo "[entrypoint] caught signal, stopping children"
  kill -TERM "$VLLM_PID" "$AGENT_PID" 2>/dev/null || true
  wait "$VLLM_PID" "$AGENT_PID" 2>/dev/null || true
}
trap cleanup SIGTERM SIGINT

# Exit as soon as either child exits.
wait -n "$VLLM_PID" "$AGENT_PID"
exit_code=$?
echo "[entrypoint] child exited with code=${exit_code}, tearing down"
cleanup
exit "$exit_code"
