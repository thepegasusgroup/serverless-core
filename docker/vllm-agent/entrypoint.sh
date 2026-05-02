#!/usr/bin/env bash
# Runs inside the rented vast.ai box. Starts vLLM + the sc-agent in parallel.
# If either process dies, the container exits so vast.ai restarts it.
set -Eeuo pipefail

: "${SC_MODEL_SLUG:?SC_MODEL_SLUG is required}"
: "${SC_CONTROL_URL:?SC_CONTROL_URL is required}"
: "${SC_AGENT_SECRET:?SC_AGENT_SECRET is required}"
: "${SC_INSTANCE_ID:?SC_INSTANCE_ID is required}"

VLLM_ARGS="${VLLM_ARGS:-}"

# If a LoRA adapter was baked into the image (Dockerfile.baked writes the
# snapshot path to /opt/sc-lora-path), inject --enable-lora and --lora-modules
# automatically so the models table doesn't need to hardcode cache paths.
if [ -f /opt/sc-lora-path ]; then
  LORA_PATH=$(cat /opt/sc-lora-path)
  LORA_NAME="${SC_LORA_NAME:-lora}"
  echo "[entrypoint] baked LoRA detected: ${LORA_NAME} → ${LORA_PATH}"
  VLLM_ARGS="${VLLM_ARGS} --enable-lora --lora-modules ${LORA_NAME}=${LORA_PATH}"
fi

echo "[entrypoint] starting vLLM with: ${VLLM_ARGS}"
python3 -m vllm.entrypoints.openai.api_server ${VLLM_ARGS} &
VLLM_PID=$!

echo "[entrypoint] starting sc-agent"
python3 -m agent.main &
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
