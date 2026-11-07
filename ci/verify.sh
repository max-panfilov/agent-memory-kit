#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
if [[ ! -f .env ]]; then
  cp .env.example .env
fi
export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-agent-memory-kit-verify}"

cleanup() {
  docker compose --profile test down --volumes --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker compose version >/dev/null
python3 scripts/check_public.py
docker compose --profile test build
docker compose --profile test up -d db
docker compose --profile test run --rm test-migrate
docker compose --profile test run --rm test
docker compose up -d mcp
docker compose --profile test run --rm smoke
PYTHONPATH="$ROOT/src" python3 -m agent_memory_kit.backup
