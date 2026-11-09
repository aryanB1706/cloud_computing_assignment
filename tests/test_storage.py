from __future__ import annotations

import json

import pytest

from worker.storage import (
    LocalStore,
    copy_tree_to_store,
    download_prefix,
    jsonl_bytes,
    parse_s3_uri,
    read_jsonl,
)


def test_parse_s3_uri():
    assert parse_s3_uri("s3://models/v1/model.tar.gz") == ("models", "v1/model.tar.gz")
    assert parse_s3_uri("s3://datasets") == ("datasets", "")
    with pytest.raises(ValueError):
        parse_s3_uri("https://example.com/file")


def test_local_store_roundtrip_and_listing(tmp_path):
    store = LocalStore(tmp_path)
    store.ensure_bucket("datasets")
    store.put_bytes("datasets", "teacher/shard-0.jsonl", b"one\n")
    store.put_bytes("datasets", "teacher/shard-1.jsonl", b"two\n")
    store.put_bytes("datasets", "v1/train.jsonl", b"three\n")
    assert store.get_bytes("datasets", "teacher/shard-0.jsonl") == b"one\n"
    assert store.get_bytes("datasets", "missing") is None
    assert store.list("datasets", "teacher/") == [
        "teacher/shard-0.jsonl",
        "teacher/shard-1.jsonl",
    ]
    store.delete("datasets", "teacher/shard-1.jsonl")
    assert store.list("datasets", "teacher/") == ["teacher/shard-0.jsonl"]


def test_local_store_atomic_overwrite(tmp_path):
    store = LocalStore(tmp_path)
    store.ensure_bucket("models")
    store.put_bytes("models", "v1/model.sha256", b"old")
    store.put_bytes("models", "v1/model.sha256", b"new")
    assert store.get_bytes("models", "v1/model.sha256") == b"new"


def test_jsonl_roundtrip_preserves_teacher_text(tmp_path):
    rows = [
        {"id": "C0002", "text": "b", "teacher_raw": '{"intent": "alarm_set", "date": null, "time": null, "place": null}'},
        {"id": "C0001", "text": "a", "teacher_raw": "line with\nnewline"},
    ]
    payload = jsonl_bytes(rows)
    assert read_jsonl(payload) == rows
    parsed = read_jsonl(payload)
    assert parsed[1]["teacher_raw"] == "line with\nnewline"


def test_copy_and_download_prefix(tmp_path):
    store = LocalStore(tmp_path / "store")
    store.ensure_bucket("models")
    source = tmp_path / "source"
    (source / "nested").mkdir(parents=True)
    (source / "config.json").write_text("{}")
    (source / "nested" / "tokenizer.json").write_text("{}")
    uploaded = copy_tree_to_store(store, "models", "v1", source)
    assert set(uploaded) == {"v1/config.json", "v1/nested/tokenizer.json"}
    destination = tmp_path / "destination"
    downloaded = download_prefix(store, "models", "v1", destination)
    assert set(downloaded) == {"config.json", "nested/tokenizer.json"}
    assert json.loads((destination / "config.json").read_text()) == {}
