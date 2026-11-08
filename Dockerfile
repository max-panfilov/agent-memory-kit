# Pinned build tooling; update the digest through a reviewed dependency change.
FROM ghcr.io/astral-sh/uv:0.12.23@sha256:61d393e44e249f2e4b526b6c7ddcecce245946826e608e11c93ad4f5bba55b21 AS uv
FROM python:3.12-slim-bookworm@sha256:6a0e06f5ba0b373d051aabd3fffb9b873f27f9a2a3a14bcbe37dee6f0e3bbc70 AS base

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --gid 10001 agent \
 && useradd --uid 10001 --gid agent --no-create-home --home-dir /tmp --shell /usr/sbin/nologin agent

COPY --from=uv /uv /usr/local/bin/uv
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_LINK_MODE=copy AGENT_MEMORY_ROOT=/app
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,id=agent-memory-uv-python312-v1,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN --mount=type=cache,id=agent-memory-uv-python312-v1,target=/root/.cache/uv uv sync --frozen --no-dev --no-editable

FROM base AS runtime
COPY migrations ./migrations
COPY content ./content
COPY schemas ./schemas
COPY scripts ./scripts
USER agent
EXPOSE 8000
ENTRYPOINT ["/app/.venv/bin/python", "-m", "agent_memory_kit.server"]
CMD ["--transport", "http", "--host", "0.0.0.0", "--port", "8000"]

FROM base AS checks
COPY ci ./ci
COPY migrations ./migrations
COPY content ./content
COPY schemas ./schemas
COPY scripts ./scripts
COPY tests ./tests
USER agent
ENTRYPOINT ["/app/.venv/bin/python", "-m", "unittest", "discover", "-s", "tests", "-v"]
