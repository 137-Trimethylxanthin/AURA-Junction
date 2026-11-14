#!/usr/bin/env bash
# One command for the whole local stack: Postgres (drawing store) + AURA backend + frontend.
#   scripts/dev.sh            # full mode (Postgres on 127.0.0.1:54329)
#   AURA_QUICK=1 scripts/dev.sh   # quick mode: no drawing store, uploads end at ready_unindexed
# Ctrl-C stops the backend and the frontend; the database container keeps running with its data.
# Secrets are never printed. The DB password comes from store/.env (when worker-store's compose stack
# created the database) or from backend/.data/pg-password (generated once, mode 600).
set -euo pipefail
set +x

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA="${ROOT}/backend/.data"
PG_PORT="${AURA_PG_PORT:-54329}"
IMAGE="aura-pg:17"
export PATH="$HOME/.local/bin:$PATH"
mkdir -p "${DATA}"
# Container engine: podman when installed, else docker (same commands for what we use).
CTR="${AURA_CONTAINER:-$(command -v podman >/dev/null 2>&1 && echo podman || echo docker)}"
ctr_image_exists() { if [ "${CTR}" = podman ]; then podman image exists "$1"; else docker image inspect "$1" >/dev/null 2>&1; fi; }
ctr_exists() { if [ "${CTR}" = podman ]; then podman container exists "$1"; else docker container inspect "$1" >/dev/null 2>&1; fi; }

# value of KEY in store/.env, or empty (never printed)
store_env() { [ -f "${ROOT}/store/.env" ] && sed -n "s/^$1=//p" "${ROOT}/store/.env" | tail -n1 || true; }

secret_file() {  # secret_file <path> <value-or-empty>: keep, write the given value, or generate (mode 600)
  local f="$1" v="$2"
  ( umask 077
    if [ -n "${v}" ]; then printf '%s\n' "${v}" > "${f}"
    elif [ ! -s "${f}" ]; then openssl rand -hex 16 > "${f}"; fi )
  chmod 600 "${f}"
  tr -d '\r\n' < "${f}"
}

pg_ok() {  # the database answers with these credentials (no pg_isready needed on the host)
  ( cd "${ROOT}/backend" && AURA_PG_DSN="$1" uv run --extra ifc python - <<'PY' >/dev/null 2>&1
import os, urllib.parse, pg8000.native
u = urllib.parse.urlparse(os.environ["AURA_PG_DSN"])
pg8000.native.Connection(user=u.username, password=urllib.parse.unquote(u.password or ""), host=u.hostname,
                         port=u.port, database=u.path.lstrip("/"), timeout=3).close()
PY
  )
}

if [ "${AURA_QUICK:-0}" != "1" ]; then
  PG_PW="$(secret_file "${DATA}/pg-password" "$(store_env JSTORE_DB_PASSWORD)")"
  READER_PW="$(secret_file "${DATA}/pg-reader-password" "$(store_env JSTORE_READER_PASSWORD)")"
  DSN="postgresql://postgres:${PG_PW}@127.0.0.1:${PG_PORT}/postgres"
  if pg_ok "${DSN}"; then
    echo "Postgres already answers on 127.0.0.1:${PG_PORT}; reusing it"
  else
    if ! ctr_image_exists "${IMAGE}"; then
      if ctr_image_exists localhost/jstore-db:17; then
        "${CTR}" tag localhost/jstore-db:17 "${IMAGE}"          # same store/db Dockerfile: no second build
      else
        echo "Building ${IMAGE} from store/db (PostGIS + pgvector) with ${CTR}..."
        "${CTR}" build -t "${IMAGE}" "${ROOT}/store/db"
      fi
    fi
    if ctr_exists aura-pg; then
      "${CTR}" start aura-pg >/dev/null
    else
      "${CTR}" run -d --name aura-pg -p "127.0.0.1:${PG_PORT}:5432" -v aura-pgdata:/var/lib/postgresql/data \
        -e POSTGRES_PASSWORD="${PG_PW}" "${IMAGE}" >/dev/null
    fi
    echo -n "Waiting for Postgres on 127.0.0.1:${PG_PORT}"
    for _ in $(seq 60); do pg_ok "${DSN}" && break; echo -n "."; sleep 1; done
    echo
    pg_ok "${DSN}" || { echo "Postgres did not come up; run AURA_QUICK=1 scripts/dev.sh for quick mode" >&2; exit 1; }
  fi
  export AURA_STORE_DSN="${DSN}"
  export AURA_STORE_READER_PASSWORD="${READER_PW}"
  # Memory on a laptop: our 2 parse slots + AURA_STORE_WORKERS ingest workers, each interpretation capped
  # at JSTORE_INTERPRET_MEM_MB of data segment; few OCR/OpenCV threads keep the workers inside that cap.
  export AURA_STORE_WORKERS="${AURA_STORE_WORKERS:-2}"
  export JSTORE_INTERPRET_MEM_MB="${JSTORE_INTERPRET_MEM_MB:-3072}"
  export OMP_NUM_THREADS="${OMP_NUM_THREADS:-4}"
  unset PG_PW READER_PW DSN
fi

export AURA_SIDECAR_URL=""   # the python sidecar is retired from the answering path
export AURA_RELOAD=0         # a reload on every code save would kill the store's ingest workers

pids=()
cleanup() { for p in "${pids[@]}"; do kill "$p" 2>/dev/null || true; done; wait 2>/dev/null || true; }
trap cleanup INT TERM EXIT

"${ROOT}/scripts/dev-backend.sh" & pids+=($!)
"${ROOT}/scripts/dev-frontend.sh" & pids+=($!)

for _ in $(seq 60); do curl -sf http://127.0.0.1:8000/health >/dev/null && break; sleep 1; done
mode="$(curl -s http://127.0.0.1:8000/health | sed -n 's/.*"store":"\([a-z]*\)".*/\1/p')"
echo
echo "AURA is up:"
echo "  frontend  http://localhost:${FRONTEND_PORT:-5173}"
echo "  backend   http://127.0.0.1:8000  (drawing store: ${mode:-unknown})"
echo "  database  127.0.0.1:${PG_PORT} (keeps running after Ctrl-C)"
wait
