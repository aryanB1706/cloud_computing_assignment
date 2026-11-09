from __future__ import annotations

import pytest

from service.model_runner import ModelRunner


def test_model_runner_reads_all_four_variables(monkeypatch):
    monkeypatch.setenv("MODEL_URI", "s3://models/v9/model.tar.gz")
    monkeypatch.setenv("MODEL_VERSION", "v9")
    monkeypatch.setenv("S3_ENDPOINT", "http://example:8333")
    monkeypatch.setenv("MAX_NEW_TOKENS", "64")
    runner = ModelRunner(
        model_uri="s3://models/v9/model.tar.gz",
        model_version="v9",
        s3_endpoint="http://example:8333",
        max_new_tokens=64,
    )
    assert runner.model_uri.endswith("v9/model.tar.gz")
    assert runner.model_version == "v9"
    assert runner.s3_endpoint == "http://example:8333"
    assert runner.max_new_tokens == 64


def test_build_runner_requires_model_uri(monkeypatch):
    from service.model_runner import build_runner_from_env

    monkeypatch.delenv("MODEL_URI", raising=False)
    with pytest.raises(RuntimeError, match="MODEL_URI"):
        build_runner_from_env()
