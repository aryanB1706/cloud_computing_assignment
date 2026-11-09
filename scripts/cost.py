#!/usr/bin/env python3
"""Cost of 1,000 requests and SLO/error-budget arithmetic (Task 7).

Formula from the assignment:

                       hourly cost of all running replicas
    cost per 1,000 = --------------------------------------- x 1000
                         requests served per hour

A replica is billed for its CPU limit and memory limit every hour it runs.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path


def load_rate_card(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def hourly_cost(rate_card: dict, cpu_limit: float, memory_gib: float, replicas: int) -> float:
    per_replica = cpu_limit * float(rate_card["vcpu_hour"]) + memory_gib * float(
        rate_card["gib_memory_hour"]
    )
    return replicas * per_replica


def cost_per_1000(hourly: float, requests_per_hour: float) -> float:
    if requests_per_hour <= 0:
        raise ValueError("requests_per_hour must be positive")
    return hourly / requests_per_hour * 1000.0


def read_locust_csv(path: str | Path) -> dict:
    """Return the Aggregated row of a Locust stats CSV."""
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row.get("Name") == "Aggregated" or row.get("Type") == "":
                return row
    raise ValueError(f"no Aggregated row in {path}")


def slo_budget(total_requests: int, failures: int, target_percent: float) -> dict:
    allowed = math.floor(total_requests * (1.0 - target_percent / 100.0))
    used = failures
    return {
        "target_percent": target_percent,
        "window": "one load-test run",
        "total_requests": total_requests,
        "failures": failures,
        "allowed_failures": allowed,
        "error_budget_used": used,
        "error_budget_remaining": allowed - used,
        "budget_used_percent": round(100.0 * used / allowed, 2) if allowed else None,
        "slo_met": failures <= allowed,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rate-card", default="data/rate_card.json")
    parser.add_argument("--csv", default="results/loadtest_stats.csv")
    parser.add_argument("--throughput", type=float, default=None, help="requests/second")
    parser.add_argument("--total-requests", type=int, default=None)
    parser.add_argument("--failures", type=int, default=None)
    parser.add_argument("--cpu-limit", type=float, default=1.0, help="cores per replica")
    parser.add_argument("--memory-limit", type=float, default=1.0, help="GiB per replica")
    parser.add_argument("--replicas", type=int, default=2)
    parser.add_argument("--slo-target", type=float, default=99.0)
    parser.add_argument("--out", default="results/cost.json")
    args = parser.parse_args()

    rate = load_rate_card(args.rate_card)
    stats = None
    if args.csv and Path(args.csv).is_file():
        stats = read_locust_csv(args.csv)

    throughput = args.throughput
    total_requests = args.total_requests
    failures = args.failures
    if stats:
        if throughput is None:
            throughput = float(stats["Requests/s"])
        if total_requests is None:
            total_requests = int(stats["Request Count"])
        if failures is None:
            failures = int(stats["Failure Count"])
    if throughput is None:
        raise SystemExit("provide --throughput or a Locust --csv file")
    if total_requests is None:
        raise SystemExit("provide --total-requests or a Locust --csv file")
    if failures is None:
        failures = 0

    rph = throughput * 3600.0
    hourly = hourly_cost(rate, args.cpu_limit, args.memory_limit, args.replicas)
    per_1000 = cost_per_1000(hourly, rph)
    budget = slo_budget(total_requests, failures, args.slo_target)

    report = {
        "rate_card": rate,
        "inputs": {
            "cpu_limit_cores": args.cpu_limit,
            "memory_limit_gib": args.memory_limit,
            "replicas": args.replicas,
            "throughput_rps": throughput,
            "requests_per_hour": round(rph, 2),
            "total_requests": total_requests,
            "failures": failures,
        },
        "working": {
            "per_replica_hourly": round(
                args.cpu_limit * rate["vcpu_hour"] + args.memory_limit * rate["gib_memory_hour"], 6
            ),
            "all_replicas_hourly": round(hourly, 6),
            "formula": "cost_per_1000 = (all_replicas_hourly / requests_per_hour) * 1000",
        },
        "cost_per_1000_requests": round(per_1000, 6),
        "currency": rate.get("currency", "INR"),
        "slo": budget,
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    print(json.dumps(report, indent=2))
    print(
        f"\nCost per 1,000 requests: {report['cost_per_1000_requests']:.4f} {report['currency']}"
    )
    print(
        "SLO: "
        f"{budget['target_percent']}% over {budget['window']}; "
        f"budget {budget['allowed_failures']} requests, used {budget['error_budget_used']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
