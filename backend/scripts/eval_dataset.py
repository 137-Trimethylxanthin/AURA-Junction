"""Ask a running AURA backend the dataset's questions about a sample of drawings and score the answers.

    uv run python scripts/eval_dataset.py --data /path/junction-dataset-preview --per-source 6 --out eval.json
    uv run python scripts/eval_dataset.py --data ... --files data/cubicasa5k/colorful-34.png data/swiss_dwellings/x.dxf

Uploads each drawing (POST /uploads), waits until it is indexed, asks every question the dataset has for it
(POST /ask) and scores like the challenge: counts exact, areas/lengths/volumes within 2 %, text reported only.
Nothing is deleted on the server, so a rerun with --reuse <eval.json> asks again without re-uploading.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections import defaultdict
from pathlib import Path

import httpx

ORIGIN = {"origin": "http://localhost:5173"}
DONE_STAGES = ("indexed", "ready_unindexed", "failed", "store_failed")


def load_questions(data: Path) -> dict[str, list[dict]]:
    by_path: dict[str, list[dict]] = defaultdict(list)
    for line in (data / "questions.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            q = json.loads(line)
            by_path[q["path"]].append(q)
    return by_path


def upload(c: httpx.Client, api: str, path: Path) -> dict:
    with path.open("rb") as f:
        r = c.post(f"{api}/uploads", headers=ORIGIN, files={"file": (path.name, f)}, timeout=120)
    r.raise_for_status()
    return r.json()


def wait_indexed(c: httpx.Client, api: str, did: str, timeout_s: float) -> str:
    t0 = time.time()
    stage = "?"
    while time.time() - t0 < timeout_s:
        st = c.get(f"{api}/drawings/{did}/status", timeout=30).json()
        stage = str(st.get("stage") or st.get("status"))
        if st.get("status") == "failed" or any(stage.startswith(s) for s in DONE_STAGES):
            return stage
        time.sleep(10)
    return f"timeout({stage})"


def score(q: dict, body: dict) -> tuple[str, object]:
    ans = body.get("answer") or {}
    kind = ans.get("kind")
    got = ans.get("value") if kind in ("count", "area", "quantity") else ans.get("number")
    if kind == "abstain":
        return "abstain", ans.get("reason")
    truth = q.get("answer")
    if q["type"] == "count":
        try:
            return ("ok" if got is not None and round(float(got)) == int(truth) else "wrong"), got
        except (TypeError, ValueError):
            return "wrong", got
    if q["type"] in ("area", "length", "volume"):
        try:
            t = float(truth)
            return ("ok" if got is not None and abs(float(got) - t) <= 0.02 * abs(t) + 1e-9 else "wrong"), got
        except (TypeError, ValueError):
            return "wrong", got
    return "text", (ans.get("value") or "")[:120]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://127.0.0.1:8000")
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--files", nargs="*", default=[])
    ap.add_argument("--per-source", type=int, default=0, help="random drawings per source (with questions)")
    ap.add_argument("--max-questions", type=int, default=12, help="per drawing")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", type=Path, default=Path("eval_dataset.json"))
    ap.add_argument("--reuse", type=Path, help="a previous --out: ask again without uploading")
    ap.add_argument("--index-timeout", type=float, default=900)
    a = ap.parse_args()

    qs = load_questions(a.data)
    files = list(a.files)
    if a.per_source:
        rnd = random.Random(a.seed)
        by_src: dict[str, list[str]] = defaultdict(list)
        for p in qs:
            if (a.data / p).is_file():
                by_src[p.split("/")[1]].append(p)
        for src, ps in sorted(by_src.items()):
            files += rnd.sample(sorted(ps), min(a.per_source, len(ps)))
    uploaded: dict[str, dict] = {}
    if a.reuse and a.reuse.is_file():
        uploaded = {k: v for k, v in json.loads(a.reuse.read_text())["uploads"].items()}

    results = []
    with httpx.Client() as c:
        for p in files:  # upload everything first: the store indexes in parallel
            if p not in uploaded:
                try:
                    uploaded[p] = upload(c, a.api, a.data / p)
                except httpx.HTTPError as e:
                    uploaded[p] = {"error": str(e)}
                print("uploaded", p, uploaded[p].get("drawing_id") or uploaded[p].get("error"), flush=True)
        for p in files:
            up = uploaded[p]
            if "drawing_id" not in up:
                continue
            stage = wait_indexed(c, a.api, up["drawing_id"], a.index_timeout)
            up["stage"] = stage
            print("indexed", p, stage, flush=True)
            same = qs.get(p) or next((qs[o] for o in qs if Path(o).with_suffix("") == Path(p).with_suffix("")), [])
            for q in same[: a.max_questions]:  # questions of the same drawing in another format count too
                t0 = time.time()
                try:
                    r = c.post(f"{a.api}/ask", json={"drawing_id": up["drawing_id"], "question": q["question"]},
                               timeout=130)
                    body = r.json()
                    verdict, got = score(q, body) if r.status_code == 200 else (f"http{r.status_code}", body.get("error"))
                except (httpx.HTTPError, ValueError) as e:
                    body, verdict, got = {}, "error", str(e)
                results.append({"path": p, "q": q["question"], "type": q["type"], "truth": q["answer"],
                                "got": got, "verdict": verdict, "route": body.get("route"),
                                "method": body.get("method"), "calls": body.get("calls"),
                                "s": round(time.time() - t0, 1)})
                print(f"  {verdict:8} truth={q['answer']!s:>8} got={got!s:>8}  {q['question'][:80]}", flush=True)

    tally: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in results:
        tally[r["path"].split("/")[1]][r["verdict"]] += 1
    a.out.write_text(json.dumps({"uploads": uploaded, "results": results,
                                 "tally": {k: dict(v) for k, v in tally.items()}}, indent=1, ensure_ascii=False))
    for src, t in sorted(tally.items()):
        n = sum(t.values())
        print(f"{src:18} {n:4} asked  ok {t.get('ok', 0):4}  wrong {t.get('wrong', 0):4}  "
              f"abstain {t.get('abstain', 0):4}  other {n - t.get('ok', 0) - t.get('wrong', 0) - t.get('abstain', 0)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
