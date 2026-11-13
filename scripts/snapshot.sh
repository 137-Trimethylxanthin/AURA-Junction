#!/usr/bin/env bash
# Re-creates the frontend T0 snapshot: .omc/snapshots/frontend-T0-<date>.tar.gz
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
out="$root/.omc/snapshots/frontend-T0-$(date +%Y%m%d).tar.gz"
mkdir -p "$(dirname "$out")"
tar -C "$root" \
  --exclude='frontend/node_modules' \
  --exclude='frontend/test-results' \
  --exclude='frontend/e2e/__screens__' \
  --exclude='frontend/playwright-report' \
  -czf "$out" frontend
echo "$out"
