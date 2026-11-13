#!/usr/bin/env bash
# Usage: scripts/smoke.sh <api-base-url> <site-url>
# Checks /health, /drawings, one /ask (swiss-46639 windows == 12) and the site index.
# Exits non-zero on the first failure.
set -euo pipefail
[ $# -eq 2 ] || { echo "usage: $0 <api-base-url> <site-url>" >&2; exit 2; }
api="${1%/}"; web="${2%/}"
fail() { echo "FAIL: $*" >&2; exit 1; }

curl -fsS --max-time 30 "$api/health" >/dev/null || fail "$api/health"
echo "ok  /health"

curl -fsS --max-time 30 "$api/drawings" | grep -q "swiss-46639" || fail "/drawings has no swiss-46639"
echo "ok  /drawings"

ask=$(curl -fsS --max-time 90 -X POST "$api/ask" -H 'Content-Type: application/json' \
  -d '{"drawing_id":"swiss-46639","question":"How many windows are there?"}') || fail "/ask request"
python3 - "$ask" <<'PY' || fail "/ask answer is not windows == 12: $ask"
import json, re, sys
d = json.loads(sys.argv[1])
def nums(x):
    if isinstance(x, bool): return
    if isinstance(x, (int, float)): yield x
    elif isinstance(x, dict):
        for k, v in x.items():
            if k in ("value", "count", "answer", "values", "numbers"): yield from nums(v)
    elif isinstance(x, list):
        for v in x: yield from nums(v)
    elif isinstance(x, str) and re.fullmatch(r"\d+", x.strip()): yield int(x)
sys.exit(0 if 12 in set(nums(d)) else 1)
PY
echo "ok  /ask windows == 12"

curl -fsS --max-time 30 "$web/" | grep -qi "<div id=\"root\"" || fail "$web/ has no #root"
echo "ok  site index"
echo "smoke passed"
