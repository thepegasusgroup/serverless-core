# Built at repo root: docker build -f apps/api/Dockerfile -t sc-api .
FROM python:3.12-slim AS base

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv==0.5.*

WORKDIR /app

# Workspace metadata first (cache-friendly).
COPY pyproject.toml uv.lock README.md /app/
COPY apps/api/pyproject.toml /app/apps/api/pyproject.toml
COPY cli/pyproject.toml /app/cli/pyproject.toml

# Install deps only.
RUN uv sync --package serverless-core-api --frozen --no-install-project --no-dev

# Now copy source and install the project itself.
COPY apps/api /app/apps/api
RUN uv sync --package serverless-core-api --frozen --no-dev

ENV PATH="/app/.venv/bin:${PATH}"
ENV PYTHONUNBUFFERED=1
EXPOSE 8000

CMD ["uvicorn", "serverless_core_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
