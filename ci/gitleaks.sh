#!/usr/bin/env bash
# Scan the complete Git history and worktree on a trusted self-hosted runner.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE="${GITLEAKS_IMAGE:-zricethezav/gitleaks@sha256:691af3c7c5a48b16f187ce3446d5f194838f91238f27270ed36eef6359a574d9}"
CONTAINER_RUNTIME="${CONTAINER_RUNTIME:-docker}"
command -v "$CONTAINER_RUNTIME" >/dev/null
"$CONTAINER_RUNTIME" run --rm \
  --network none \
  --read-only \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  --tmpfs /tmp:rw,noexec,nosuid,size=64m \
  --volume "$ROOT:/repo:ro" \
  "$IMAGE" \
  detect --source /repo --config /repo/.gitleaks.toml --no-banner --redact --verbose
