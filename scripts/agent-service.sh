#!/usr/bin/env bash
# Launch AURA's agent-service sidecar on 127.0.0.1:8090 with rootless Podman sandbox and Python 3.12 venv.
# Usage:
#   scripts/agent-service.sh           # real Gemini model (loads ~/.config/junction/env quietly)
#   scripts/agent-service.sh --demo    # scripted instant demo model (0 API calls)
set -euo pipefail
set +x

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_DIR="${REPO_ROOT}/agent-service"

DEMO_MODE=0
if [ "${1:-}" = "--demo" ]; then
  DEMO_MODE=1
  shift
fi

ENV_FILE="${JUNCTION_ENV_FILE:-${HOME}/.config/junction/env}"
if [ -f "${ENV_FILE}" ]; then
  set +x
  set -a
  # shellcheck disable=SC1090
  . "${ENV_FILE}"
  set +a
fi

if [ "${DEMO_MODE}" -eq 1 ]; then
  export JUNCTION_MODEL="demo"
else
  export JUNCTION_MODEL="${JUNCTION_MODEL:-gemini-3.8-flash}"
fi

export JUNCTION_REASONING_EFFORT="${JUNCTION_REASONING_EFFORT:-low}"
export JUNCTION_OPENAI_BASE_URL="${JUNCTION_OPENAI_BASE_URL:-https://generativelanguage.googleapis.com/v1beta/openai}"
export JUNCTION_API_STYLE="${JUNCTION_API_STYLE:-chat}"

if [ -n "${GEMINI_API_KEY:-}" ] && [ -z "${JUNCTION_OPENAI_API_KEY:-}" ]; then
  export JUNCTION_OPENAI_API_KEY="${GEMINI_API_KEY}"
fi

export JUNCTION_SANDBOX="${JUNCTION_SANDBOX:-docker}"
export JUNCTION_DATA_DIR="${JUNCTION_DATA_DIR:-${SERVICE_DIR}/.data}"
export JUNCTION_REGISTER_ROOT="${JUNCTION_REGISTER_ROOT:-${SERVICE_DIR}/.data/register}"
export JUNCTION_MAX_TURNS="${JUNCTION_MAX_TURNS:-40}"
export JUNCTION_MAX_CONCURRENT="${JUNCTION_MAX_CONCURRENT:-2}"
export JUNCTION_SHELL_TIMEOUT="${JUNCTION_SHELL_TIMEOUT:-60}"

mkdir -p "${JUNCTION_DATA_DIR}" "${JUNCTION_REGISTER_ROOT}"

TOKEN_FILE="${JUNCTION_TOKEN_FILE:-${JUNCTION_DATA_DIR}/token}"
if [ -z "${JUNCTION_API_TOKEN:-}" ]; then
  if [ -s "${TOKEN_FILE}" ]; then
    JUNCTION_API_TOKEN="$(tr -d '\r\n' < "${TOKEN_FILE}")"
  else
    (
      umask 077
      if command -v openssl >/dev/null 2>&1; then
        openssl rand -hex 24 > "${TOKEN_FILE}"
      else
        "${SERVICE_DIR}/.venv/bin/python" -c "import secrets; print(secrets.token_hex(24))" > "${TOKEN_FILE}"
      fi
    )
    chmod 600 "${TOKEN_FILE}"
    JUNCTION_API_TOKEN="$(tr -d '\r\n' < "${TOKEN_FILE}")"
  fi
  export JUNCTION_API_TOKEN
else
  (
    umask 077
    printf '%s\n' "${JUNCTION_API_TOKEN}" > "${TOKEN_FILE}"
  )
  chmod 600 "${TOKEN_FILE}"
fi

HOST="${JUNCTION_HOST:-127.0.0.1}"
PORT="${JUNCTION_PORT:-${PORT:-8090}}"

cd "${SERVICE_DIR}"
exec "${SERVICE_DIR}/.venv/bin/uvicorn" service.app:app --no-proxy-headers --host "${HOST}" --port "${PORT}" "$@"
