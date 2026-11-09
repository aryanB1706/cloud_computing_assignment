from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))


@pytest.fixture(scope="session")
def schema() -> dict:
    return json.loads((ROOT / "data" / "schema.json").read_text(encoding="utf-8"))


@pytest.fixture()
def local_store(tmp_path, monkeypatch):
    from worker.storage import LocalStore

    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("LOCAL_STORAGE_DIR", str(tmp_path / "storage"))
    store = LocalStore(tmp_path / "storage")
    store.ensure_bucket("datasets")
    store.ensure_bucket("models")
    return store
