"""The README configuration table, generated from aura.config.Settings (so it cannot drift).

Usage (from backend/): uv run python -I scripts/env_table.py --write   (rewrites README.md's table)
tests/test_contract.py checks that README.md holds exactly this table.
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from aura.config import Settings

README = BACKEND.parent / 'README.md'
START, END = '<!-- env-table:start -->', '<!-- env-table:end -->'

# Where it is set and what it does. Every Settings alias must have an entry (the test fails otherwise).
DOC = {
    'GEMINI_API_KEY': ('Modal secret `aura-gemini`', 'Gemini access, server side only'),
    'AURA_PRESENTER_TOKEN': ('Modal secret `aura-presenter`', 'Presenter token: bypasses per-IP limits, unlocks image uploads and the model on/off switch'),
    'AURA_IP_SALT': ('Modal secret `aura-presenter`, **required on Modal**', 'Secret salt for hashed client IPs (the app refuses to start on Modal with the default)'),
    'AURA_BUDGET_SEED': ('deploy shell, **required on Modal**', 'Gemini requests already used on this key before this database'),
    'AURA_UNLIMITED': ('backend env (`scripts/dev-backend.sh` sets 1)', 'Lift every request/upload cap (local dev and demo only); spend is still counted'),
    'AURA_EVENT_CAP': ('backend env', 'Event-wide request cap (seed + counted + pending) (0 = unlimited)'),
    'AURA_DAILY_CAP': ('backend env', 'Public requests per Europe/Helsinki day (0 = unlimited)'),
    'AURA_DEV_CAP': ('backend env', 'Requests in the `dev` bucket (0 = unlimited)'),
    'AURA_MEASUREMENT_CAP': ('backend env', 'Requests in the `measurement` bucket (0 = unlimited)'),
    'AURA_IP_AGENT_PER_HOUR': ('backend env', 'Router-miss model requests (choice, agent, rewrite) per client IP per hour (0 = unlimited)'),
    'AURA_RASTER_PUBLIC_CAP': ('backend env', 'Image analyses allowed without the presenter token (0 = presenter only)'),
    'AURA_BUDGET_TZ': ('backend env', 'Time zone of the daily cap'),
    'AURA_DECISION_MODEL': ('backend env', 'Model for the Decisions API (checks, question choice)'),
    'AURA_AGENT_MODEL': ('backend env', 'Model for the function-calling agent and /rewrite'),
    'AURA_ANSWERER': ('backend env', 'Answer routing mode (`hybrid`, `agent`, or `exact`)'),
    'AURA_STORE_DSN': ('backend env (`scripts/dev.sh` sets it)', 'Postgres DSN of the drawing store (jstore); unset = quick mode'),
    'AURA_STORE_READER_PASSWORD': ('backend env (`scripts/dev.sh` sets it)', 'Password of the read-only `jreader` role used by the sql tool'),
    'AURA_STORE_DATA_DIR': ('backend env', 'Where the drawing store keeps upload copies and page renders'),
    'AURA_STORE_WORKERS': ('backend env', 'Worker processes that ingest uploads into the drawing store'),
    'AURA_SIDECAR_URL': ('backend env', 'Base URL of the isolated deep-agent sidecar (`agent-service`)'),
    'AURA_SIDECAR_TOKEN_FILE': ('backend env', 'Path to the shared bearer token file for `agent-service`'),
    'AURA_SIDECAR_MODEL': ('backend env', 'Sidecar model used for cost booking until the run reports its own (match JUNCTION_MODEL)'),
    'AURA_AGENT_RUNS_PER_IP_HOUR': ('backend env', 'Deep-agent sidecar runs per client IP per hour (presenter exempt) (0 = unlimited)'),
    'AURA_AGENT_RUNS_PER_DAY': ('backend env', 'Public deep-agent sidecar runs per Europe/Helsinki day (0 = unlimited)'),
    'AURA_TURN_DEADLINE_S': ('backend env', 'Deadline of one /ask or /chat turn (s)'),
    'AURA_AGENT_DEADLINE_S': ('backend env', 'Deadline of a /chat turn once it escalates to the sidecar agent (s)'),
    'AURA_TRACE_DETAIL': ('backend env', 'Reasoning trace detail: `dev` (every stage with its data) or `demo` (short)'),
    'AURA_VOLUME_COMMIT_S': ('backend env', 'Modal Volume commit interval (s)'),
    'AURA_CLEANUP_S': ('backend env', 'Interval of the expiry sweep (s)'),
    'AURA_UPLOAD_TTL_S': ('backend env', 'Lifetime of uploads, answers, conversations and cached answers (s)'),
    'AURA_UPLOADS_PER_IP_HOUR': ('backend env', 'Uploads per client IP per hour (presenter exempt) (0 = unlimited)'),
    'AURA_MAX_QUEUED_UPLOADS': ('backend env', 'Uploads queued or parsing at once (0 = unlimited)'),
    'AURA_UPLOAD_QUOTA_BYTES': ('backend env', 'Total bytes of all stored uploads (0 = unlimited)'),
    'DATA_DIR': ('set in `modal_app.py`', 'SQLite database, uploads and logs (the Volume, `/data`)'),
    'AURA_BUILD_DIR': ('backend env', 'Preprocessed library drawings'),
    'ALLOWED_ORIGINS': ('backend env', 'Exact CORS origins (comma list or JSON list)'),
    'ALLOWED_ORIGIN_REGEX': ('backend env', 'CORS regex; the default pins the Pages project `aura-j26` and its preview subdomains. Set it if the project is named otherwise'),
}
# Read outside Settings (on every call or by tooling).
EXTRA = [
    ('AURA_MODELS', 'backend env', 'Kill switch: `off` disables every model call (read on each call; fixed per container on Modal)'),
    ('AURA_LIVE', 'tests', '`1` enables live Gemini tests'),
    ('VITE_API_URL', 'frontend build', 'API base URL'),
]


def default(name: str, f) -> str:
    if name in ('GEMINI_API_KEY', 'AURA_PRESENTER_TOKEN', 'AURA_IP_SALT', 'DATA_DIR', 'AURA_BUILD_DIR',
                'ALLOWED_ORIGINS', 'ALLOWED_ORIGIN_REGEX', 'AURA_SIDECAR_URL', 'AURA_SIDECAR_TOKEN_FILE'):
        return ''
    if isinstance(f.default, Path):
        # A path inside the repo is shown from the repo root, so the table reads the same on every machine.
        try:
            return f'`{f.default.relative_to(BACKEND.parent).as_posix()}`'
        except ValueError:
            return f'`{f.default}`'
    return f'`{f.default:g}`' if isinstance(f.default, float) else f'`{f.default}`'


def table() -> str:
    rows = ['| Variable | Where | Default | What it does |', '|---|---|---|---|']
    for name, f in Settings.model_fields.items():
        alias = f.alias or name
        where, what = DOC[alias]
        rows.append(f'| `{alias}` | {where} | {default(alias, f)} | {what} |')
    rows += [f'| `{n}` | {w} |  | {d} |' for n, w, d in EXTRA]
    return '\n'.join(rows)


def readme_with_table(text: str) -> str:
    a, b = text.index(START) + len(START), text.index(END)
    return text[:a] + '\n' + table() + '\n' + text[b:]


if __name__ == '__main__':
    if '--write' in sys.argv:
        README.write_text(readme_with_table(README.read_text(encoding='utf-8')), encoding='utf-8')
    else:
        print(table())
