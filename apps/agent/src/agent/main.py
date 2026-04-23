"""serverless-core agent.

Runs alongside vLLM on every rented vast.ai box:
  1. Wait for local vLLM to pass /health.
  2. POST /internal/instances/register to the control plane with our public
     IP and port (as assigned by vast.ai).
  3. POST /internal/instances/heartbeat every 20s.

Env vars (set at rent time by the control plane, plus vast.ai's own):
  SC_CONTROL_URL          e.g. https://sc-api-thepegasus.fly.dev
  SC_AGENT_SECRET         shared secret, checked by /internal/* endpoints
  SC_INSTANCE_ID          UUID of the instances row we map to
  SC_MODEL_SLUG           e.g. qwen2.5-7b-instruct
  VLLM_PORT               defaults to 8000
  VAST_PUBLIC_IPADDR      set by vast at boot
  VAST_TCP_PORT_8000      external port mapped to internal 8000
"""
from __future__ import annotations

import logging
import os
import signal
import sys
import time
from typing import Any

import httpx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [agent] %(levelname)s: %(message)s",
)
log = logging.getLogger("agent")


def _env(name: str, required: bool = True, default: str | None = None) -> str:
    v = os.environ.get(name, default)
    if required and not v:
        log.error("Missing required env var %s", name)
        sys.exit(2)
    return v or ""


class Agent:
    HEARTBEAT_INTERVAL_S = 20.0
    VLLM_BOOT_TIMEOUT_S = 1800  # 30 min — large models can take a while to load
    REGISTER_MAX_ATTEMPTS = 12

    def __init__(self) -> None:
        self.control_url = _env("SC_CONTROL_URL").rstrip("/")
        self.secret = _env("SC_AGENT_SECRET")
        self.instance_id = _env("SC_INSTANCE_ID")
        self.model_slug = _env("SC_MODEL_SLUG")
        self.vllm_port = int(_env("VLLM_PORT", required=False, default="8000"))

        self.public_ip = _env("VAST_PUBLIC_IPADDR", required=False)
        self.public_port = int(
            _env(
                f"VAST_TCP_PORT_{self.vllm_port}",
                required=False,
                default=str(self.vllm_port),
            )
        )

        self.vllm_health_url = f"http://127.0.0.1:{self.vllm_port}/health"
        self._stopped = False
        self._http = httpx.Client(
            timeout=10.0,
            headers={
                "X-Agent-Secret": self.secret,
                "Content-Type": "application/json",
            },
        )

    def stop(self, *_args: Any) -> None:
        log.info("Stop signal received")
        self._stopped = True

    def wait_for_vllm(self) -> bool:
        deadline = time.time() + self.VLLM_BOOT_TIMEOUT_S
        while time.time() < deadline and not self._stopped:
            if self._vllm_healthy():
                log.info("vLLM is healthy")
                return True
            log.info("Waiting for vLLM at %s ...", self.vllm_health_url)
            time.sleep(5)
        return False

    def register(self) -> None:
        log.info("Registering (instance=%s, model=%s, %s:%s)",
                 self.instance_id, self.model_slug,
                 self.public_ip, self.public_port)
        backoff = 2.0
        for attempt in range(self.REGISTER_MAX_ATTEMPTS):
            if self._stopped:
                return
            try:
                r = self._http.post(
                    f"{self.control_url}/internal/instances/register",
                    json={
                        "instance_id": self.instance_id,
                        "ip": self.public_ip,
                        "port": self.public_port,
                        "model_slug": self.model_slug,
                        "agent_version": "0.1.0",
                    },
                )
                r.raise_for_status()
                log.info("Registered OK")
                return
            except httpx.HTTPError as e:
                log.warning("Register attempt %d failed: %s (retrying in %.1fs)",
                            attempt + 1, e, backoff)
                time.sleep(backoff)
                backoff = min(backoff * 1.5, 30.0)
        log.error("Register failed after %d attempts", self.REGISTER_MAX_ATTEMPTS)
        sys.exit(3)

    def heartbeat_loop(self) -> None:
        log.info("Entering heartbeat loop (every %ss)", self.HEARTBEAT_INTERVAL_S)
        while not self._stopped:
            healthy = self._vllm_healthy()
            try:
                self._http.post(
                    f"{self.control_url}/internal/instances/heartbeat",
                    json={
                        "instance_id": self.instance_id,
                        "vllm_healthy": healthy,
                    },
                )
            except httpx.HTTPError as e:
                log.warning("Heartbeat errored: %s", e)
            time.sleep(self.HEARTBEAT_INTERVAL_S)

    def _vllm_healthy(self) -> bool:
        try:
            r = httpx.get(self.vllm_health_url, timeout=3.0)
            return r.status_code == 200
        except httpx.HTTPError:
            return False


def main() -> None:
    agent = Agent()
    signal.signal(signal.SIGTERM, agent.stop)
    signal.signal(signal.SIGINT, agent.stop)

    if not agent.wait_for_vllm():
        log.error("vLLM never became healthy; exiting")
        sys.exit(4)

    agent.register()
    agent.heartbeat_loop()
    log.info("Agent exiting cleanly")


if __name__ == "__main__":
    main()
