#!/usr/bin/env bash
# Start the AURA Vite frontend dev server (default: http://localhost:5173 -> API http://localhost:8000).
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
export VITE_API_URL="${VITE_API_URL:-http://localhost:8000}"
port="${PORT:-5173}"

echo "Starting AURA frontend on http://localhost:${port} (VITE_API_URL=${VITE_API_URL})"
cd "$root/frontend"
exec npm run dev -- --port "$port" "$@"
