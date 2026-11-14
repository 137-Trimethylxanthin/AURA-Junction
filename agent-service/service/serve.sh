#!/usr/bin/env bash
# Run the drawing-QA service detached (survives disconnects), through scripts/agent-service.sh
# (sandbox, token and keys are set there; secrets are never echoed or committed).
#   bash service/serve.sh start|stop|status|logs     PORT env var overrides 8090
set -euo pipefail
set +x
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
PORT="${PORT:-8090}"
PID=".data/uvicorn.pid"
LOG=".data/uvicorn.log"
mkdir -p .data
case "${1:-status}" in
start)
  if [ -f "$PID" ] && kill -0 "$(cat "$PID")" 2>/dev/null; then echo "already running (pid $(cat "$PID"))"; exit 0; fi
  # Always via scripts/agent-service.sh: it loads the keys quietly, sets the rootless Podman
  # sandbox (JUNCTION_SANDBOX=docker) and the bearer token. Never start uvicorn bare here.
  PORT="$PORT" setsid nohup "$ROOT_DIR/../scripts/agent-service.sh" >>"$LOG" 2>&1 </dev/null &
  echo $! > "$PID"
  for _ in $(seq 20); do curl -sf "http://127.0.0.1:$PORT/health" >/dev/null && break; sleep 1; done
  echo "started pid $(cat "$PID") on 127.0.0.1:$PORT"
  ;;
stop)  [ -f "$PID" ] && kill "$(cat "$PID")" 2>/dev/null && echo stopped || echo "not running" ;;
status)
  [ -f "$PID" ] && kill -0 "$(cat "$PID")" 2>/dev/null && echo "running pid $(cat "$PID")" || echo "not running"
  curl -s "http://127.0.0.1:$PORT/health" || true
  echo
  ;;
logs)  tail -n 50 "$LOG" ;;
esac
