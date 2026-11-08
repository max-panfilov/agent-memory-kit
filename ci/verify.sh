#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
if [[ ! -f .env ]]; then
  cp .env.example .env
fi
# Never inherit the namespace of a running demo or share it with another run.
export COMPOSE_PROJECT_NAME="agent-memory-kit-verify-$(python3 -c 'import secrets; print(secrets.token_hex(12))')"
export MCP_PORT=0
resources_created=false

cleanup() {
  if [[ "$resources_created" == true ]]; then
    docker compose --profile test down --volumes --remove-orphans >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

docker compose version >/dev/null
for resource in container volume network; do
  options=(--quiet --filter "label=com.docker.compose.project=$COMPOSE_PROJECT_NAME")
  if [[ "$resource" == container ]]; then options+=(--all); fi
  existing="$(docker "$resource" ls "${options[@]}")"
  if [[ -n "$existing" ]]; then
    echo 'Verification namespace already exists; refusing to use or delete it' >&2
    exit 1
  fi
done
python3 scripts/check_public.py
docker compose --profile test build
resources_created=true
docker compose --profile test up -d db
docker compose --profile test run --rm test-migrate
docker compose --profile test run --rm test
docker compose up -d mcp
docker compose --profile test run --rm smoke
PYTHONPATH="$ROOT/src" python3 -m agent_memory_kit.backup
docker build --file "$ROOT/ci/Dockerfile.mermaid" --tag agent-memory-kit-mermaid:local "$ROOT"
docker run --rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges --tmpfs /tmp:rw,noexec,nosuid,size=64m --volume "$ROOT:/data:ro" agent-memory-kit-mermaid:local
