"""Pre-warm curated demo questions into AURA's SQLite `answer_cache` before live demos.

DRY RUN BY DEFAULT: prints the planned `(drawing_id, question)` turns and makes 0 HTTP/model calls
unless `--live` is explicitly passed (or `--dry-run` is omitted with `--live`). Respects AURA's
budget caps by polling `/budget` before each turn and stopping if remaining event budget < 80 slots.

Usage (from backend/):
  uv run python -I scripts/prewarm.py --dry-run
  uv run python -I scripts/prewarm.py --live --url http://127.0.0.1:8000 --drawings swiss-46639 swiss-22513
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

import httpx

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

DEFAULT_DRAWINGS = (
    "swiss-46639",
    "swiss-22513",
)

DEMO_QUESTIONS = (
    "How many apartments are on this floor plan?",
    "Give me all room areas and color by room type",
    "Show all north windows",
    "How many double doors are on this floor plan?",
    "Show all stairs and where they lead to",
    "What is the width of the entrance door?",
)


def _presenter_token_from_env() -> str | None:
    tok = os.environ.get("AURA_PRESENTER_TOKEN", "").strip()
    if tok:
        return tok
    env_file = Path.home() / ".config" / "junction" / "env"
    if env_file.is_file():
        try:
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("export "):
                    line = line[7:].strip()
                if line.startswith("AURA_PRESENTER_TOKEN="):
                    val = line.split("=", 1)[1].strip().strip("'\"")
                    if val:
                        return val
        except OSError:
            pass
    return None


async def run_prewarm(url: str, drawings: list[str], questions: list[str]) -> int:
    headers: dict[str, str] = {}
    pres = _presenter_token_from_env()
    if pres:
        headers["x-aura-presenter"] = pres

    completed = 0
    async with httpx.AsyncClient(base_url=url.rstrip("/"), timeout=130.0) as client:
        for did in drawings:
            b_resp = await client.get("/budget")
            if b_resp.status_code == 200:
                b = b_resp.json()
                rem = int(b.get("event_cap", 0)) - int(b.get("event_used", 0))
                if rem < 80:
                    print(f"Stopping early: remaining event budget ({rem}) < 80 slots.")
                    return completed
            for q in questions:
                r = await client.post(
                    "/ask",
                    headers=headers,
                    json={"drawing_id": did, "question": q},
                )
                if r.status_code != 200:
                    print(f"  [WARN] {did} | {q!r} -> HTTP {r.status_code}")
                    continue
                body = r.json()
                route = body.get("route")
                calls = body.get("calls", 0)
                kind = (body.get("answer") or {}).get("kind")
                print(f"  [OK]   {did} | {q!r} -> kind={kind} route={route} calls={calls}")
                completed += 1
    return completed


def main() -> None:
    ap = argparse.ArgumentParser(description="Pre-warm AURA SQLite answer_cache for demo drawings.")
    ap.add_argument("--url", default="http://127.0.0.1:8000", help="Base URL of running AURA server")
    ap.add_argument("--drawings", nargs="*", default=list(DEFAULT_DRAWINGS), help="Drawing IDs to warm")
    ap.add_argument("--questions", nargs="*", default=list(DEMO_QUESTIONS), help="Questions to ask each drawing")
    ap.add_argument("--dry-run", action="store_true", help="Print plan without making network/model calls")
    ap.add_argument("--live", action="store_true", help="Execute live /ask requests against --url")
    args = ap.parse_args()

    planned = [(d, q) for d in args.drawings for q in args.questions]
    print(f"{len(planned)} demo turns planned across {len(args.drawings)} drawing(s) against {args.url}:")
    for did, q in planned:
        print(f"  - {did}: {q}")

    if args.dry_run or not args.live:
        print("dry run: 0 calls made (pass --live to execute).")
        return

    done = asyncio.run(run_prewarm(args.url, list(args.drawings), list(args.questions)))
    print(f"Completed {done}/{len(planned)} pre-warm turns.")


if __name__ == "__main__":
    main()
