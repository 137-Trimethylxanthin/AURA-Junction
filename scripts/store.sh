#!/usr/bin/env bash
# Run the lossless project store (store/: Postgres+PostGIS and its API) locally with podman-compose.
# Usage:
#   scripts/store.sh start     # write store/.env if needed, build, start, wait for /health
#   scripts/store.sh stop      # stop the containers (volumes and data are kept)
#   scripts/store.sh status    # containers and /health
#   scripts/store.sh logs [n]  # last n lines of the API log (default 100)
# The API listens on 127.0.0.1:${JSTORE_API_PORT:-8100}, the database on 127.0.0.1:54329. Secrets live only in
# store/.env (git-ignored, mode 600); this script never prints them. The API token is JSTORE_API_TOKEN there.
set -euo pipefail
set +x

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STORE="${ROOT}/store"
ENV_FILE="${STORE}/.env"
SECRETS="${JUNCTION_ENV_FILE:-${HOME}/.config/junction/env}"
PORT="${JSTORE_API_PORT:-8100}"
DATASET="${JSTORE_DATASET_HOST_DIR:-${ROOT}/data/junction-dataset}"

# value of KEY in store/.env, or empty (never printed)
getv() { [ -f "${ENV_FILE}" ] && sed -n "s/^$1=//p" "${ENV_FILE}" | tail -n1 || true; }

ensure_env() {
  local gemini token dbpw rdpw model base
  gemini="$(getv GEMINI_API_KEY)"
  if [ -z "${gemini}" ] && [ -f "${SECRETS}" ]; then
    gemini="$(set +x; set -a; . "${SECRETS}" >/dev/null 2>&1; printf '%s' "${GEMINI_API_KEY:-}")"
  fi
  token="$(getv JSTORE_API_TOKEN)";      [ -n "${token}" ] || token="$(openssl rand -hex 24)"
  dbpw="$(getv JSTORE_DB_PASSWORD)";     [ -n "${dbpw}" ]  || dbpw="$(openssl rand -hex 16)"
  rdpw="$(getv JSTORE_READER_PASSWORD)"; [ -n "${rdpw}" ]  || rdpw="$(openssl rand -hex 16)"
  model="$(getv JSTORE_MODEL)";          [ -n "${model}" ] || model="gemini-4-hydrogen"
  base="$(getv JSTORE_OPENAI_BASE_URL)"; [ -n "${base}" ]  || base="https://generativelanguage.googleapis.com/v1beta/openai"
  # The ingest workers cap their data segment at 4 GB (store/jstore/pipeline.py). On a many-core laptop the
  # OCR/OpenCV thread pools (one thread per core, each with its own stack) exceed that cap and PNG/JPG parsing
  # fails with "pthread_create failed: Cannot allocate memory". Few threads keep them inside the cap.
  omp="$(getv OMP_NUM_THREADS)";         [ -n "${omp}" ]   || omp=4
  [ -d "${DATASET}" ] || { DATASET="${STORE}/.data/empty-dataset"; mkdir -p "${DATASET}"; }
  ( umask 077
    { printf '# written by scripts/store.sh; git-ignored; never commit\n'
      printf 'GEMINI_API_KEY=%s\n' "${gemini}"
      printf 'JSTORE_API_TOKEN=%s\n' "${token}"
      printf 'JSTORE_MODEL=%s\n' "${model}"
      printf 'JSTORE_OPENAI_BASE_URL=%s\n' "${base}"
      printf 'JSTORE_DB_PASSWORD=%s\n' "${dbpw}"
      printf 'JSTORE_READER_PASSWORD=%s\n' "${rdpw}"
      printf 'OMP_NUM_THREADS=%s\n' "${omp}"
      printf 'JSTORE_API_PORT=%s\n' "${PORT}"
      printf 'JSTORE_DATASET_HOST_DIR=%s\n' "${DATASET}"
    } > "${ENV_FILE}.tmp" )
  mv "${ENV_FILE}.tmp" "${ENV_FILE}"
  chmod 600 "${ENV_FILE}"
  [ -n "${gemini}" ] || echo "note: no GEMINI_API_KEY found; set JSTORE_MODEL=demo in store/.env for a keyless run" >&2
}

compose() {
  # compose interpolation (ports, DB password, dataset mount) comes from the exported store/.env
  ( cd "${STORE}" && set -a && . "${ENV_FILE}" && set +a && podman-compose "$@" )
}

health() { curl -s -m 3 "http://127.0.0.1:${PORT}/health" 2>/dev/null || true; }

case "${1:-status}" in
  start)
    before="$(sha256sum "${ENV_FILE}" 2>/dev/null || true)"
    ensure_env
    if [ "${before}" != "$(sha256sum "${ENV_FILE}")" ]; then
      compose up -d --build --force-recreate      # podman-compose does not recreate on an env change by itself
    else
      compose up -d --build
    fi
    printf 'waiting for http://127.0.0.1:%s/health ' "${PORT}"
    for _ in $(seq 1 120); do
      if [ -n "$(health)" ]; then echo; echo "store up: $(health)"; break; fi
      printf '.'; sleep 2
    done
    [ -n "$(health)" ] || { echo; echo "store did not answer /health; see: scripts/store.sh logs" >&2; exit 1; }
    echo "API http://127.0.0.1:${PORT}  database 127.0.0.1:54329  dataset ${DATASET}"
    echo "token: JSTORE_API_TOKEN in ${ENV_FILE}; AURA base URL http://127.0.0.1:${PORT}/aura/t/<token>"
    ;;
  stop)
    [ -f "${ENV_FILE}" ] || ensure_env
    compose down
    ;;
  status)
    podman ps -a --filter name=jstore --format '{{.Names}}\t{{.Status}}\t{{.Ports}}'
    h="$(health)"; echo "health: ${h:-no answer on 127.0.0.1:${PORT}}"
    ;;
  logs)
    podman logs --tail "${2:-100}" jstore_api_1
    ;;
  *)
    echo "usage: $0 start|stop|status|logs [n]" >&2; exit 2
    ;;
esac
