# Built from the repo root:
#   docker build -f docker/vllm-agent/Dockerfile -t ghcr.io/thepegasusgroup/sc-vllm-agent:<sha> .
FROM vllm/vllm-openai:latest

# Ensure `python` resolves even though the base image only ships `python3`.
RUN ln -sf "$(command -v python3)" /usr/local/bin/python

# Install the agent.
COPY apps/agent /opt/agent
RUN pip install --no-cache-dir /opt/agent

COPY docker/vllm-agent/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["/entrypoint.sh"]
