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
    'AURA_EVENT_CAP': ('backend env', 'Event-wide request cap (seed + counted + pending)'),
    'AURA_DAILY_CAP': ('backend env', 'Public requests per Europe/Helsinki day'),
    'AURA_DEV_CAP': ('backend env', 'Requests in the `dev` bucket'),
    'AURA_MEASUREMENT_CAP': ('backend env', 'Requests in the `measurement` bucket'),
    'AURA_IP_AGENT_PER_HOUR': ('backend env', 'Router-miss model requests (choice, agent, rewrite) per client IP per hour'),
    'AURA_RASTER_PUBLIC_CAP': ('backend env', 'Image analyses allowed without the presenter token (0 = presenter only)'),
    'AURA_BUDGET_TZ': ('backend env', 'Time zone of the daily cap'),
    'AURA_DECISION_MODEL': ('backend env', 'Model for the Decisions API (checks, question choice)'),
    'AURA_AGENT_MODEL': ('backend env', 'Model for the function-calling agent and /rewrite'),
    'AURA_TURN_DEADLINE_S': ('backend env', 'Deadline of one /ask or /chat turn (s)'),
    'AURA_VOLUME_COMMIT_S': ('backend env', 'Modal Volume commit interval (s)'),
    'AURA_CLEANUP_S': ('backend env', 'Interval of the expiry sweep (s)'),
    'AURA_UPLOAD_TTL_S': ('backend env', 'Lifetime of uploads, answers, conversations and cached answers (s)'),
    'AURA_UPLOADS_PER_IP_HOUR': ('backend env', 'Uploads per client IP per hour (presenter exempt)'),
    'AURA_MAX_QUEUED_UPLOADS': ('backend env', 'Uploads queued or parsing at once'),
    'AURA_UPLOAD_QUOTA_BYTES': ('backend env', 'Total bytes of all stored uploads'),
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
                'ALLOWED_ORIGINS', 'ALLOWED_ORIGIN_REGEX'):
        return ''
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
