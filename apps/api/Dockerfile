# Built from the repo root: fly runs `docker build -f apps/api/Dockerfile .`
FROM python:3.12-slim

# Pull uv from its official image (faster and cacheable).
COPY --from=ghcr.io/astral-sh/uv:0.11.7 /uv /uvx /bin/

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Workspace metadata first — lets Docker cache deps independently of source.
COPY pyproject.toml uv.lock ./
COPY apps/api/pyproject.toml ./apps/api/pyproject.toml
COPY cli/pyproject.toml ./cli/pyproject.toml
# uv workspace resolution touches every member; ship a stub src tree for cli.
COPY cli/src ./cli/src

# Install dependencies only (no project code yet).
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --package serverless-core-api --frozen --no-install-project --no-dev

# Now bring in the actual API source and install the project itself.
COPY apps/api ./apps/api
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --package serverless-core-api --frozen --no-dev

ENV PATH="/app/.venv/bin:${PATH}"
ENV PYTHONUNBUFFERED=1
EXPOSE 8000

CMD ["uvicorn", "serverless_core_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
