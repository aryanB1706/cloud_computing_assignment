"""Command-parsing inference service (Task 3).

Endpoints
    POST /parse   {"text": "..."} -> four-key JSON (HTTP 200) or
                  {"error": "invalid_model_output"} (HTTP 500)
    GET  /healthz HTTP 200 while the process is alive
    GET  /readyz  HTTP 503 until the model finishes loading, then HTTP 200

Configuration comes from MODEL_URI, MODEL_VERSION, S3_ENDPOINT and
MAX_NEW_TOKENS (plus FAULT_INJECT for validation testing).  S3 credentials are
injected by Kubernetes from a Secret; none are present in this file.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .validation import extract_first_valid

LOG = logging.getLogger("service")
SCHEMA_PATH = Path(os.environ.get("SCHEMA_PATH", Path(__file__).with_name("schema.json")))


class ParseRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=512)


class ServiceState:
    def __init__(self) -> None:
        self.runner = None
        self.ready = False
        self.load_error: str | None = None
        self.lock = threading.Lock()


def _load_schema() -> dict:
    with open(SCHEMA_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def _headers() -> dict[str, str]:
    pod_name = os.environ.get("POD_NAME") or os.environ.get("HOSTNAME") or socket.gethostname()
    return {
        "X-Model-Version": os.environ.get("MODEL_VERSION", "unknown"),
        "X-Served-By": pod_name,
    }


def create_app(runner=None, schema: dict | None = None, load_async: bool = True) -> FastAPI:
    """Application factory; ``runner`` and ``schema`` are injectable for tests."""
    state = ServiceState()
    state.runner = runner
    schema_data = schema or _load_schema()

    def _load_model() -> None:
        try:
            if state.runner is None:
                from .model_runner import build_runner_from_env

                state.runner = build_runner_from_env()
            state.runner.load()
            with state.lock:
                state.ready = True
                state.load_error = None
            LOG.info("model ready (version=%s)", os.environ.get("MODEL_VERSION", "unknown"))
        except Exception as exc:  # keep the process alive so /readyz can report 503
            with state.lock:
                state.ready = False
                state.load_error = str(exc)
            LOG.exception("model load failed: %s", exc)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        LOG.info(
            "starting service: MODEL_URI=%s MODEL_VERSION=%s S3_ENDPOINT=%s MAX_NEW_TOKENS=%s",
            os.environ.get("MODEL_URI"),
            os.environ.get("MODEL_VERSION"),
            os.environ.get("S3_ENDPOINT"),
            os.environ.get("MAX_NEW_TOKENS"),
        )
        if load_async:
            threading.Thread(target=_load_model, name="model-loader", daemon=True).start()
        else:
            _load_model()
        yield
        LOG.info("service shutting down")

    app = FastAPI(title="Command Parser", version="1.0.0", lifespan=lifespan)
    app.state.service = state
    app.state.schema = schema_data

    @app.get("/healthz")
    def healthz():
        return JSONResponse(status_code=200, content={"status": "ok"})

    @app.get("/readyz")
    def readyz():
        with state.lock:
            if state.ready:
                return JSONResponse(status_code=200, content={"status": "ready"})
            body = {"status": "not_ready"}
            if state.load_error:
                body["error"] = state.load_error
            return JSONResponse(status_code=503, content=body)

    @app.post("/parse")
    def parse(request: ParseRequest):
        headers = _headers()
        with state.lock:
            ready = state.ready
        if not ready:
            return JSONResponse(
                status_code=503, content={"error": "not_ready"}, headers=headers
            )

        try:
            raw = state.runner.parse(request.text)
        except Exception:
            LOG.exception("inference failed")
            return JSONResponse(
                status_code=500, content={"error": "inference_failed"}, headers=headers
            )

        # Deliberate corruption for testing the output validator.
        if os.environ.get("FAULT_INJECT", "0") == "1":
            raw = "not json"

        parsed = extract_first_valid(raw, schema_data)
        if parsed is None:
            LOG.warning("model returned invalid output: %.200r", raw)
            return JSONResponse(
                status_code=500,
                content={"error": "invalid_model_output"},
                headers=headers,
            )
        return JSONResponse(status_code=200, content=parsed, headers=headers)

    return app


app = create_app()
