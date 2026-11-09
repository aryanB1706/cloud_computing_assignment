#!/usr/bin/env python3
"""Send the Task-3 sequential request test to a running service.

    python scripts/bench_local.py --url http://127.0.0.1:8000 --n 50 --pid <service-pid>

If ``--pid`` is supplied, RSS is sampled from /proc/<pid>/status and the whole
process tree is included with psutil when available.  The request workload is
validation.jsonl by default.  The output is written to results/ and printed.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import threading
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from service.validation import extract_first_valid  # noqa: E402


def load_texts(path: str, limit: int | None = None) -> list[str]:
    texts = [json.loads(line)["text"] for line in open(path, encoding="utf-8") if line.strip()]
    return texts[:limit] if limit else texts


def rss_mib(pid: int) -> float:
    try:
        import psutil

        process = psutil.Process(pid)
        total = process.memory_info().rss
        for child in process.children(recursive=True):
            try:
                total += child.memory_info().rss
            except psutil.Error:
                pass
        return total / (1024 * 1024)
    except ImportError:
        status = Path(f"/proc/{pid}/status").read_text(encoding="utf-8")
        for line in status.splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024.0
    raise RuntimeError("cannot read RSS (install psutil or use Linux /proc)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--n", type=int, default=50)
    parser.add_argument("--texts", default="data/validation.jsonl")
    parser.add_argument("--pid", type=int, default=None, help="service PID for RSS sampling")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--out", default="results/service_local_inference.json")
    parser.add_argument("--predictions", default="results/service_predictions.jsonl")
    args = parser.parse_args()

    schema = json.loads((ROOT / "data/schema.json").read_text(encoding="utf-8"))
    texts = load_texts(args.texts)
    if not texts:
        raise SystemExit("no texts")

    peak = {"rss_mib": 0.0}
    cpu_samples: list[float] = []
    stop = threading.Event()

    def cpu_percent(pid: int) -> float:
        try:
            import psutil

            process = psutil.Process(pid)
            total = process.cpu_percent(interval=None)
            for child in process.children(recursive=True):
                try:
                    total += child.cpu_percent(interval=None)
                except psutil.Error:
                    pass
            return total
        except ImportError:
            return 0.0

    def sample() -> None:
        if args.pid:
            cpu_percent(args.pid)  # prime the non-blocking counter
        while not stop.is_set():
            try:
                peak["rss_mib"] = max(peak["rss_mib"], rss_mib(args.pid))
                if args.pid:
                    cpu_samples.append(cpu_percent(args.pid))
            except Exception:
                pass
            time.sleep(0.05)

    sampler = None
    if args.pid:
        sampler = threading.Thread(target=sample, daemon=True)
        sampler.start()

    latencies = []
    failures = []
    statuses = {}
    served_by = {}
    versions = {}
    predictions = []
    started = time.time()

    for index in range(args.n):
        text = texts[index % len(texts)]
        try:
            begin = time.perf_counter()
            resp = requests.post(
                f"{args.url.rstrip('/')}/parse",
                json={"text": text},
                timeout=args.timeout,
            )
            latency = (time.perf_counter() - begin) * 1000.0
            latencies.append(latency)
            statuses[resp.status_code] = statuses.get(resp.status_code, 0) + 1
            served_by[resp.headers.get("X-Served-By")] = (
                served_by.get(resp.headers.get("X-Served-By"), 0) + 1
            )
            versions[resp.headers.get("X-Model-Version")] = (
                versions.get(resp.headers.get("X-Model-Version"), 0) + 1
            )
            if resp.status_code == 200:
                body = resp.json()
                if extract_first_valid(json.dumps(body), schema) is None:
                    failures.append({"index": index, "reason": "schema", "body": body})
                predictions.append({"id": f"L{index + 1:03d}", "prediction": json.dumps(body)})
            else:
                failures.append(
                    {"index": index, "status": resp.status_code, "body": resp.text[:200]}
                )
        except Exception as exc:
            failures.append({"index": index, "reason": "exception", "error": str(exc)})

    stop.set()
    if sampler:
        sampler.join(timeout=1)
    elapsed = time.time() - started

    lat_sorted = sorted(latencies)

    def percentile(pct: float) -> float:
        if not lat_sorted:
            return 0.0
        position = (len(lat_sorted) - 1) * pct
        low = int(position)
        high = min(low + 1, len(lat_sorted) - 1)
        return lat_sorted[low] + (lat_sorted[high] - lat_sorted[low]) * (position - low)

    report = {
        "url": args.url,
        "requests": args.n,
        "elapsed_s": round(elapsed, 3),
        "throughput_rps": round(args.n / elapsed, 3) if elapsed else 0.0,
        "p50_ms": round(percentile(0.50), 2),
        "p95_ms": round(percentile(0.95), 2),
        "mean_ms": round(statistics.fmean(latencies), 2) if latencies else 0.0,
        "max_ms": round(max(latencies), 2) if latencies else 0.0,
        "errors": len(failures),
        "error_rate_percent": round(100.0 * len(failures) / args.n, 2),
        "status_counts": statuses,
        "served_by": served_by,
        "model_versions": versions,
        "peak_rss_mib": round(peak["rss_mib"], 1),
        "peak_cpu_percent": round(max(cpu_samples), 1) if cpu_samples else None,
        "mean_cpu_percent": round(statistics.fmean(cpu_samples), 1) if cpu_samples else None,
        "failures": failures[:10],
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    pred = Path(args.predictions)
    pred.parent.mkdir(parents=True, exist_ok=True)
    with open(pred, "w", encoding="utf-8") as fh:
        for row in predictions:
            fh.write(json.dumps(row) + "\n")

    print(json.dumps(report, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
