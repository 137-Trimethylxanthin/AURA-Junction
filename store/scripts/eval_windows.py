"""Window (and door) counts of the Gemini symbol detector on CubiCasa plans against the dataset's answers.

    python store/scripts/eval_windows.py [--set sample|heldout|all] [--out results.json] [--data DIR]

Runs jstore.gemini_detect.detect() on each image directly (no Postgres).  Gemini responses are cached under
<data>/cache/gemini keyed by image bytes + prompt, so a rerun of unchanged code costs nothing.  Needs
GEMINI_API_KEY in the environment.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from pathlib import Path

DATASET = Path(os.environ.get("JUNCTION_DATASET", "/root/test-data/junction-dataset-preview"))
FIXED = ["high_quality_architectural-13309", "high_quality_architectural-8029", "high_quality_architectural-7924",
         "high_quality_architectural-9488", "high_quality_architectural-16", "high_quality_architectural-12027"]
N_SAMPLE, N_HELDOUT, SEED = 15, 5, 7


def truths() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for line in (DATASET / "questions.jsonl").read_text().splitlines():
        q = json.loads(line)
        p = q.get("path", "")
        if not p.startswith("data/cubicasa5k/"):
            continue
        name = Path(p).stem
        text = q["question"]
        if text.startswith("How many windows are shown"):
            out.setdefault(name, {})["window"] = int(q["answer"])
        elif text.startswith("How many doors are shown"):
            out.setdefault(name, {})["door"] = int(q["answer"])
    return {k: v for k, v in out.items() if "window" in v}


def pick(t: dict) -> tuple[list[str], list[str]]:
    rest = sorted(k for k in t if k not in FIXED)
    rng = random.Random(SEED)
    rng.shuffle(rest)
    sample = FIXED + rest[:N_SAMPLE - len(FIXED)]
    heldout = rest[N_SAMPLE - len(FIXED):N_SAMPLE - len(FIXED) + N_HELDOUT]
    return sample, heldout


def report(rows: list[dict], label: str) -> dict:
    res = {}
    for cls in ("window", "door"):
        rs = [r for r in rows if r["truth"].get(cls) is not None]
        if not rs:
            continue
        exact = sum(r["got"][cls] == r["truth"][cls] for r in rs)
        mae = sum(abs(r["got"][cls] - r["truth"][cls]) for r in rs) / len(rs)
        bias = sum(r["got"][cls] - r["truth"][cls] for r in rs) / len(rs)
        res[cls] = dict(n=len(rs), exact=exact, mae=round(mae, 2), bias=round(bias, 2))
        print(f"  {label:8s} {cls:6s} exact {exact}/{len(rs)}  MAE {mae:.2f}  bias {bias:+.2f}")
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default="sample", choices=["sample", "heldout", "all", "extra"])
    ap.add_argument("--extra", type=int, default=15, help="images in the 'extra' held-out set")
    ap.add_argument("--out", default=None)
    ap.add_argument("--data", default=os.environ.get("JSTORE_DATA_DIR",
                                                     str(Path(__file__).resolve().parents[1] / ".data")))
    args = ap.parse_args()
    os.environ["JSTORE_DATA_DIR"] = args.data
    sys.path.insert(0, os.environ.get("JSTORE_SRC", str(Path(__file__).resolve().parents[1])))
    from jstore import gemini_detect as gd

    t = truths()
    sample, heldout = pick(t)
    rest = [k for k in sorted(t) if k not in sample and k not in heldout]
    random.Random(SEED + 1).shuffle(rest)
    sets = {"sample": sample, "heldout": heldout, "all": sample + heldout, "extra": rest[:args.extra]}[args.set]
    rows, calls, cached = [], 0, 0
    t0 = time.time()
    for name in sets:
        r = gd.detect(DATASET / "data" / "cubicasa5k" / f"{name}.png")
        calls += r.get("calls", 0)
        cached += r.get("cached", 0)
        got = {cls: sum(o["label"] == cls for o in r["objects"]) for cls in ("window", "door")}
        which = "sample" if name in sample else "heldout" if name in heldout else "extra"
        row = dict(name=name, set=which, truth=t[name], got=got,
                   calls=r.get("calls"), errors=r.get("errors"), candidates=r["candidates"], views=r["views"],
                   areas=r["areas"], size=r["size"])
        rows.append(row)
        print(f"{name:40s} win {t[name]['window']:3d} -> {got['window']:3d}   door {t[name].get('door', '-')!s:>3} -> "
              f"{got['door']:3d}   calls {r.get('calls')} errs {len(r.get('errors') or [])}", flush=True)
    print(f"{gd.MODEL} {gd.PROMPT_VERSION}: {len(rows)} images, {calls} calls ({cached} cached), "
          f"{time.time() - t0:.0f} s")
    summary = {}
    for s in ("sample", "heldout", "extra"):
        rs = [r for r in rows if r["set"] == s]
        if rs:
            summary[s] = report(rs, s)
    if args.out:
        Path(args.out).write_text(json.dumps(dict(summary=summary, rows=rows), indent=1))


if __name__ == "__main__":
    main()
