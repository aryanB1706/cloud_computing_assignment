from __future__ import annotations

import hashlib
import json

from worker.clean import clean_records, id_sort_key, sha256_hex, validate_reply
from worker.storage import jsonl_bytes


def reply(**overrides):
    obj = {"intent": "alarm_set", "date": None, "time": None, "place": None}
    obj.update(overrides)
    return json.dumps(obj)


def raw(identifier, text, teacher_raw):
    return {"id": identifier, "text": text, "teacher_raw": teacher_raw}


def test_rejection_reasons_and_accounting(schema):
    records = [
        raw("C0001", "set an alarm", "not json"),  # (a)
        raw("C0002", "set an alarm", '{"intent": "alarm_set"}'),  # (b)
        raw("C0003", "set an alarm", reply(intent="bogus")),  # (c)
        raw("C0004", "set an alarm for six", reply(date="tomorrow")),  # (d)
        raw("C0010", "what is the weather in london", reply(intent="weather_query", place="london")),
        raw("C0006", "what is the weather in london", reply(intent="weather_query", place="london")),
        raw("C0007", "set an alarm for six", reply(time="six")),
    ]
    kept, report = clean_records(records, schema)
    counts = report["counts"]
    assert counts["input"] == 7
    assert counts["invalid_json"] == 1
    assert counts["wrong_keys"] == 1
    assert counts["bad_intent"] == 1
    assert counts["bad_slot"] == 1
    assert counts["duplicates"] == 1
    assert counts["kept"] == 2
    assert counts["input"] == sum(
        counts[k] for k in ("invalid_json", "wrong_keys", "bad_intent", "bad_slot", "duplicates", "kept")
    )
    assert report["invariant_holds"] is True
    # duplicate text: smallest id (C0002) survives
    assert [row["id"] for row in kept] == ["C0006", "C0007"]


def test_duplicate_keeps_smallest_id_regardless_of_input_order(schema):
    good = reply(intent="weather_query", place="london")
    records = [
        raw("C0100", "weather in london", good),
        raw("C0005", "weather in london", good),
        raw("C0050", "weather in london", good),
    ]
    kept, report = clean_records(records, schema)
    assert [row["id"] for row in kept] == ["C0005"]
    assert report["counts"]["duplicates"] == 2


def test_slot_matching_is_case_insensitive_and_requires_substring(schema):
    intents = set(schema["intents"])
    reason, parsed = validate_reply(reply(date="Tomorrow"), "set an alarm tomorrow", intents)
    assert reason is None and parsed["date"] == "Tomorrow"
    reason, _ = validate_reply(reply(place="paris"), "set an alarm tomorrow", intents)
    assert reason == "bad_slot"
    reason, _ = validate_reply(reply(time=6), "set an alarm at 6", intents)
    assert reason == "bad_slot"


def test_empty_slot_is_rejected(schema):
    intents = set(schema["intents"])
    reason, _ = validate_reply(reply(date="  "), "set an alarm tomorrow", intents)
    assert reason == "bad_slot"


def test_output_is_sorted_by_numeric_id(schema):
    records = [raw(f"C{1000 + i}", f"command number {i}", reply()) for i in range(3)]
    records.append(raw("C0002", "another command", reply()))
    kept, _ = clean_records(records, schema)
    assert [r["id"] for r in kept] == ["C0002", "C1000", "C1001", "C1002"]
    assert id_sort_key("C1000") < id_sort_key("C2000")


def test_checksum_is_deterministic(schema):
    records = [raw("C0001", "weather in london", reply(intent="weather_query", place="london"))]
    kept, _ = clean_records(records, schema)
    first = sha256_hex(jsonl_bytes(kept))
    second = sha256_hex(jsonl_bytes(kept))
    assert first == second
    assert hashlib.sha256(jsonl_bytes(kept)).hexdigest() == first


def test_clean_main_writes_matching_checksum(local_store, tmp_path, monkeypatch):
    """End-to-end: shards in object storage -> train.jsonl + train.sha256."""
    from worker import clean

    commands = [
        {"id": "C0001", "text": "set an alarm for six am tomorrow"},
        {"id": "C0002", "text": "what is the weather in london"},
        {"id": "C0003", "text": "delete my appointment tomorrow"},
        {"id": "C0004", "text": "set an alarm for six am tomorrow"},  # duplicate text
        {"id": "C0005", "text": "play some music"},
    ]
    local_store.put_bytes(
        "datasets",
        "commands_pool.jsonl",
        jsonl_bytes(commands),
    )
    local_store.put_bytes(
        "datasets",
        "teacher/shard-0.jsonl",
        jsonl_bytes(
            [
                raw("C0001", commands[0]["text"], reply(date="tomorrow", time="six am")),
                raw("C0003", commands[2]["text"], reply(intent="calendar_remove", date="tomorrow")),
            ]
        ),
    )
    local_store.put_bytes(
        "datasets",
        "teacher/shard-1.jsonl",
        jsonl_bytes(
            [
                raw("C0002", commands[1]["text"], reply(intent="weather_query", place="london")),
                raw("C0004", commands[3]["text"], reply(date="tomorrow", time="six am")),
                raw("C0005", commands[4]["text"], "broken"),
            ]
        ),
    )

    out = tmp_path / "train.jsonl"
    stats = tmp_path / "clean_counts.json"
    status = clean.main(
        ["--out", str(out), "--stats", str(stats), "--no-upload"]
    )
    assert status == 0
    data = out.read_bytes()
    expected = sha256_hex(data)
    # simulate the uploaded checksum file
    local_store.put_bytes("datasets", "v1/train.sha256", f"{expected}  train.jsonl\n".encode())
    checksum_text = local_store.get_bytes("datasets", "v1/train.sha256").decode()
    assert checksum_text.split()[0] == expected

    rows = [json.loads(line) for line in data.decode().splitlines()]
    assert [r["id"] for r in rows] == ["C0001", "C0002", "C0003"]
    report = json.loads(stats.read_text())
    assert report["counts"]["input"] == 5
    assert report["counts"]["invalid_json"] == 1
    assert report["counts"]["duplicates"] == 1
    assert report["counts"]["kept"] == 3
    assert report["coverage"]["checked"] is True


def test_clean_main_fails_on_missing_shard_coverage(local_store, tmp_path):
    from worker import clean

    local_store.put_bytes(
        "datasets",
        "commands_pool.jsonl",
        jsonl_bytes([{"id": "C0001", "text": "a"}, {"id": "C0002", "text": "b"}]),
    )
    local_store.put_bytes(
        "datasets",
        "teacher/shard-0.jsonl",
        jsonl_bytes([raw("C0001", "a", reply())]),
    )
    status = clean.main(
        ["--out", str(tmp_path / "train.jsonl"), "--stats", str(tmp_path / "stats.json"), "--no-upload"]
    )
    assert status == 1
