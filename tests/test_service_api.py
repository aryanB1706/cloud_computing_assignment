from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from service.app import create_app

VALID = json.dumps({"intent": "weather_query", "date": None, "time": None, "place": "london"})


class FakeRunner:
    def __init__(self, output: str = VALID, fail_load: bool = False):
        self.output = output
        self.fail_load = fail_load
        self.loaded = False

    def load(self) -> None:
        if self.fail_load:
            raise RuntimeError("simulated download failure")
        self.loaded = True

    def parse(self, text: str) -> str:
        return self.output


@pytest.fixture()
def client(monkeypatch, schema):
    monkeypatch.setenv("MODEL_VERSION", "v1")
    monkeypatch.setenv("POD_NAME", "command-parser-abc123")
    monkeypatch.delenv("FAULT_INJECT", raising=False)
    app = create_app(runner=FakeRunner(), schema=schema, load_async=False)
    with TestClient(app) as test_client:
        yield test_client


def test_health_and_readiness(client):
    assert client.get("/healthz").status_code == 200
    response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_parse_returns_exactly_four_keys_and_headers(client):
    response = client.post("/parse", json={"text": "what is the weather in london"})
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"intent", "date", "time", "place"}
    assert body["intent"] == "weather_query"
    assert response.headers["X-Model-Version"] == "v1"
    assert response.headers["X-Served-By"] == "command-parser-abc123"


def test_invalid_model_output_returns_exact_500(monkeypatch, schema):
    monkeypatch.setenv("MODEL_VERSION", "v1")
    app = create_app(runner=FakeRunner("not json"), schema=schema, load_async=False)
    with TestClient(app) as client:
        response = client.post("/parse", json={"text": "hello"})
    assert response.status_code == 500
    assert response.json() == {"error": "invalid_model_output"}


def test_fault_inject_replaces_output(monkeypatch, schema):
    monkeypatch.setenv("MODEL_VERSION", "v1")
    monkeypatch.setenv("FAULT_INJECT", "1")
    app = create_app(runner=FakeRunner(VALID), schema=schema, load_async=False)
    with TestClient(app) as client:
        response = client.post("/parse", json={"text": "hello"})
    assert response.status_code == 500
    assert response.json() == {"error": "invalid_model_output"}


def test_wrapped_output_is_accepted(monkeypatch, schema):
    monkeypatch.setenv("MODEL_VERSION", "v1")
    app = create_app(runner=FakeRunner(f"```json\n{VALID}\n```"), schema=schema, load_async=False)
    with TestClient(app) as client:
        response = client.post("/parse", json={"text": "hello"})
    assert response.status_code == 200
    assert set(response.json()) == {"intent", "date", "time", "place"}


def test_not_ready_before_model_load(monkeypatch, schema):
    monkeypatch.setenv("MODEL_VERSION", "v1")
    app = create_app(runner=FakeRunner(fail_load=True), schema=schema, load_async=False)
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/readyz").status_code == 503
        response = client.post("/parse", json={"text": "hello"})
        assert response.status_code == 503
        assert response.json() == {"error": "not_ready"}


def test_empty_text_is_rejected(client):
    assert client.post("/parse", json={"text": ""}).status_code == 422
    assert client.post("/parse", json={}).status_code == 422
