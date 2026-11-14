#!/usr/bin/env bash
# Start the AURA FastAPI backend in local dev mode (default: http://localhost:8000).
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="$HOME/.local/bin:$PATH"

# Load local API keys/tokens if present (never printed or echoed).
env_file="${JUNCTION_ENV_FILE:-$HOME/.config/junction/env}"
if [[ -f "$env_file" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$env_file"
  set +a
fi

# Prefer drawings-live (all 17 DXF + IFC drawings) when available.
if [[ -z "${AURA_BUILD_DIR:-}" && -d "$root/backend/build/drawings-live" ]]; then
  export AURA_BUILD_DIR="$root/backend/build/drawings-live"
fi

# Wire the local agent-service sidecar (scripts/agent-service.sh: 127.0.0.1:8090, token in .data/token).
export AURA_SIDECAR_URL="${AURA_SIDECAR_URL:-http://127.0.0.1:${JUNCTION_PORT:-8090}}"
export AURA_SIDECAR_TOKEN_FILE="${AURA_SIDECAR_TOKEN_FILE:-${JUNCTION_TOKEN_FILE:-$root/agent-service/.data/token}}"
export AURA_SIDECAR_MODEL="${AURA_SIDECAR_MODEL:-${JUNCTION_MODEL:-gemini-3.8-flash}}"

# Local dev and demo run without request/upload caps (spend is still counted in /budget).
# The code defaults stay bounded, so a deploy that forgets this is safe. Set AURA_UNLIMITED=0 to test caps.
export AURA_UNLIMITED="${AURA_UNLIMITED:-1}"

host="${HOST:-127.0.0.1}"
port="${PORT:-8000}"

echo "Starting AURA backend on http://${host}:${port} (build_dir=${AURA_BUILD_DIR:-default})"
cd "$root/backend"
exec uv run --extra ifc uvicorn aura.main:create_app \
  --factory \
  --reload \
  --host "$host" \
  --port "$port" \
  --no-proxy-headers \
  "$@"
