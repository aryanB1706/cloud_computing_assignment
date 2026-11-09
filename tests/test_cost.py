from __future__ import annotations

import json

import pytest

from scripts.cost import (
    cost_per_1000,
    hourly_cost,
    load_rate_card,
    read_locust_csv,
    slo_budget,
)


def test_hourly_cost_uses_limits_and_replicas():
    rate = load_rate_card("data/rate_card.json")
    # 2 replicas x (1 core x 3.50 + 1 GiB x 0.45) = 7.90 per hour
    assert hourly_cost(rate, 1.0, 1.0, 2) == pytest.approx(7.90)
    assert hourly_cost(rate, 0.5, 2.0, 1) == pytest.approx(0.5 * 3.50 + 2.0 * 0.45)


def test_cost_per_1000_formula():
    # 7.90/hour, 10 req/s => 36,000 req/hour => 0.219444...
    assert cost_per_1000(7.90, 36000) == pytest.approx(7.90 / 36000 * 1000)
    assert cost_per_1000(7.90, 10 * 3600) == pytest.approx(0.219444, rel=1e-5)


def test_slo_error_budget():
    budget = slo_budget(1000, 5, 99.0)
    assert budget["allowed_failures"] == 10
    assert budget["error_budget_remaining"] == 5
    assert budget["slo_met"] is True
    assert slo_budget(1000, 15, 99.0)["slo_met"] is False


def test_locust_csv_aggregate_row(tmp_path):
    path = tmp_path / "loadtest_stats.csv"
    header = (
        "Type,Name,Request Count,Failure Count,Median Response Time,Average Response Time,"
        "Min Response Time,Max Response Time,Average Content Size,Requests/s,Failures/s,"
        "50%,66%,75%,80%,90%,95%,98%,99%,99.9%,99.99%,100%\n"
    )
    path.write_text(
        header
        + "POST,/parse,900,2,100,120,50,900,100,7.5,0.0,100,110,115,120,130,200,300,400,500,600,900\n"
        + ",Aggregated,1000,5,100,120,50,900,100,8.3,0.04,100,110,115,120,130,200,300,400,500,600,900\n"
    )
    row = read_locust_csv(path)
    assert row["Request Count"] == "1000"
    assert row["Failure Count"] == "5"
    assert float(row["Requests/s"]) == pytest.approx(8.3)


def test_cost_report_written(tmp_path, monkeypatch):
    from scripts import cost

    csv_path = tmp_path / "stats.csv"
    csv_path.write_text(
        "Type,Name,Request Count,Failure Count,Requests/s\n"
        ",Aggregated,1000,5,8.3\n"
    )
    out = tmp_path / "cost.json"
    monkeypatch.setattr(
        "sys.argv",
        ["cost.py", "--csv", str(csv_path), "--out", str(out)],
    )
    assert cost.main() == 0
    report = json.loads(out.read_text())
    assert report["inputs"]["replicas"] == 2
    assert report["currency"] == "INR"
    assert report["slo"]["allowed_failures"] == 10
