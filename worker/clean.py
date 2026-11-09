#!/usr/bin/env python3
"""Clean and validate raw teacher shards (Task 1, item 6).

Reads every ``teacher/shard-*.jsonl`` object from the datasets bucket, rejects
records for the reasons (a)-(d) in the assignment, removes duplicate commands
(keeping the smallest id), sorts the survivors by id, and writes:

    datasets/v1/train.jsonl
    datasets/v1/train.sha256      (standard "<sha256>  train.jsonl" line)
    datasets/v1/clean_counts.json

The exact teacher reply is preserved in ``teacher_raw``; it is never rewritten.

Rejection precedence is a -> b -> c -> d.  A record is counted exactly once,
so the accounting invariant holds:

    input = invalid_json + wrong_keys + bad_intent + bad_slot + duplicates + kept

Usage
    python worker/clean.py
    python worker/clean.py --allow-incomplete     # smoke tests / partial runs
    STORAGE_BACKEND=local LOCAL_STORAGE_DIR=./local-storage python worker/clean.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys
from pathlib import Path

try:  # module and script mode
    from .storage import DATASETS_BUCKET, get_store, jsonl_bytes, read_jsonl
except ImportError:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from storage import DATASETS_BUCKET, get_store, jsonl_bytes, read_jsonl  # type: ignore

LOG = logging.getLogger("clean")
SLOT_KEYS = ("date", "time", "place")
EXPECTED_KEYS = {"intent", "date", "time", "place"}
SHARD_RE = re.compile(r"shard-(\d+)\.jsonl$")


def configure_logging() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )


def load_schema(path: str | None = None) -> dict:
    path = path or os.environ.get("SCHEMA_PATH", "data/schema.json")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def normalize(value: object) -> str:
    return " ".join(str(value).strip().lower().split())


def id_sort_key(identifier: object) -> tuple:
    raw = str(identifier)
    digits = "".join(ch for ch in raw if ch.isdigit())
    return (int(digits) if digits else 0, raw)


def _slot_is_grounded(slot: object, command: str) -> bool:
    """True if the slot is null, or a non-empty string found in the command."""
    if slot is None:
        return True
    if not isinstance(slot, str):
        return False
    candidate = normalize(slot)
    if not candidate:
        return False
    return candidate in normalize(command)


def validate_reply(teacher_raw: object, command: str, intents: set[str]) -> tuple[str | None, dict | None]:
    """Return ``(reason, parsed)``; reason is None when the reply is valid."""
    if not isinstance(teacher_raw, str):
        return "invalid_json", None
    try:
        parsed = json.loads(teacher_raw.strip())
    except (json.JSONDecodeError, TypeError):
        return "invalid_json", None
    if not isinstance(parsed, dict):
        return "invalid_json", None
    if set(parsed.keys()) != EXPECTED_KEYS:
        return "wrong_keys", None
    if not isinstance(parsed.get("intent"), str) or parsed["intent"] not in intents:
        return "bad_intent", None
    for key in SLOT_KEYS:
        if not _slot_is_grounded(parsed.get(key), command):
            return "bad_slot", None
    return None, parsed


def clean_records(
    raw_records: list[dict],
    schema: dict,
    *,
    seed_examples: int = 3,
) -> tuple[list[dict], dict]:
    """Pure cleaning function (unit-testable, no I/O)."""
    intents = set(schema["intents"])
    counts = {
        "input": len(raw_records),
        "invalid_json": 0,
        "wrong_keys": 0,
        "bad_intent": 0,
        "bad_slot": 0,
        "duplicates": 0,
        "kept": 0,
    }
    examples: dict[str, list[dict]] = {k: [] for k in (
        "invalid_json", "wrong_keys", "bad_intent", "bad_slot", "duplicates"
    )}
    valid: list[tuple[str, dict]] = []

    def record_rejection(reason: str, record: dict, detail: str = "") -> None:
        counts[reason] += 1
        if len(examples[reason]) < seed_examples:
            examples[reason].append(
                {"id": record.get("id"), "teacher_raw": record.get("teacher_raw"), "detail": detail}
            )

    for record in raw_records:
        command = str(record.get("text", ""))
        reason, _parsed = validate_reply(record.get("teacher_raw"), command, intents)
        if reason:
            record_rejection(reason, record)
            continue
        valid.append((str(record.get("id")), record))

    # Duplicate commands: keep the record with the smallest id.
    best_by_text: dict[str, tuple[str, dict]] = {}
    for identifier, record in valid:
        key = normalize(record.get("text", ""))
        current = best_by_text.get(key)
        if current is None:
            best_by_text[key] = (identifier, record)
            continue
        keep, drop = (
            ((identifier, record), current)
            if id_sort_key(identifier) < id_sort_key(current[0])
            else (current, (identifier, record))
        )
        best_by_text[key] = keep
        record_rejection("duplicates", drop[1], detail=f"duplicate of {keep[0]}")

    kept_records = sorted(
        (r for _identifier, r in best_by_text.values()), key=lambda r: id_sort_key(r.get("id"))
    )
    counts["kept"] = len(kept_records)

    total = sum(
        counts[k]
        for k in ("invalid_json", "wrong_keys", "bad_intent", "bad_slot", "duplicates", "kept")
    )
    if total != counts["input"]:
        raise AssertionError(
            f"accounting invariant broken: input={counts['input']} classified={total} {counts}"
        )
    report = {
        "counts": counts,
        "invariant": "input = invalid_json + wrong_keys + bad_intent + bad_slot + duplicates + kept",
        "invariant_holds": total == counts["input"],
        "examples": examples,
    }
    return kept_records, report


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-prefix", default="teacher/")
    parser.add_argument("--version", default="v1")
    parser.add_argument("--schema", default=None)
    parser.add_argument("--out", default=None, help="local path for train.jsonl")
    parser.add_argument("--stats", default=None, help="local path for clean_counts.json")
    parser.add_argument(
        "--allow-incomplete",
        action="store_true",
        help="do not fail when some command ids are missing (smoke tests)",
    )
    parser.add_argument(
        "--no-upload", action="store_true", help="only write local files, do not upload"
    )
    args = parser.parse_args(argv)

    store = get_store()
    schema = load_schema(args.schema)
    shard_keys = sorted(
        k for k in store.list(DATASETS_BUCKET, args.source_prefix) if SHARD_RE.search(k)
    )
    if not shard_keys:
        LOG.error("no shard objects under s3://%s/%s", DATASETS_BUCKET, args.source_prefix)
        return 1
    LOG.info("reading %d shard objects: %s", len(shard_keys), ", ".join(shard_keys))

    raw_records: list[dict] = []
    per_shard: dict[str, int] = {}
    for key in shard_keys:
        data = store.get_bytes(DATASETS_BUCKET, key)
        rows = read_jsonl(data or b"")
        per_shard[key] = len(rows)
        raw_records.extend(rows)

    # Coverage check against the command pool, if it is available.
    coverage: dict = {"checked": False}
    pool = store.get_bytes(DATASETS_BUCKET, "commands_pool.jsonl")
    if pool:
        expected_ids = [str(r["id"]) for r in read_jsonl(pool)]
        seen: dict[str, int] = {}
        for r in raw_records:
            seen[str(r.get("id"))] = seen.get(str(r.get("id")), 0) + 1
        repeated = sorted(k for k, v in seen.items() if v > 1)
        missing = sorted(set(expected_ids) - set(seen))
        unexpected = sorted(set(seen) - set(expected_ids))
        coverage = {
            "checked": True,
            "expected": len(expected_ids),
            "present": len(seen),
            "repeated_ids": repeated[:20],
            "missing_ids": missing[:20],
            "unexpected_ids": unexpected[:20],
        }
        if repeated:
            LOG.error("command ids repeated across shards: %s", repeated[:10])
            return 1
        if (missing or unexpected) and not args.allow_incomplete:
            LOG.error(
                "shard coverage incomplete (missing=%d unexpected=%d); "
                "use --allow-incomplete for smoke tests",
                len(missing),
                len(unexpected),
            )
            return 1

    kept_records, report = clean_records(raw_records, schema)
    report["shards"] = per_shard
    report["coverage"] = coverage
    report["version"] = args.version

    train_bytes = jsonl_bytes(kept_records)
    checksum = sha256_hex(train_bytes)
    checksum_line = f"{checksum}  train.jsonl\n"
    stats_bytes = (json.dumps(report, indent=2) + "\n").encode("utf-8")

    out_path = Path(args.out) if args.out else Path("results") / "train.jsonl"
    stats_path = Path(args.stats) if args.stats else Path("results") / "clean_counts.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    stats_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(train_bytes)
    stats_path.write_bytes(stats_bytes)

    if not args.no_upload:
        prefix = args.version.strip("/")
        store.put_bytes(DATASETS_BUCKET, f"{prefix}/train.jsonl", train_bytes)
        store.put_bytes(DATASETS_BUCKET, f"{prefix}/train.sha256", checksum_line.encode("utf-8"))
        store.put_bytes(DATASETS_BUCKET, f"{prefix}/clean_counts.json", stats_bytes)
        LOG.info(
            "uploaded s3://%s/%s/train.jsonl (sha256 %s)", DATASETS_BUCKET, prefix, checksum
        )

    c = report["counts"]
    LOG.info(
        "input=%d (a)=%d (b)=%d (c)=%d (d)=%d duplicates=%d kept=%d sha256=%s",
        c["input"],
        c["invalid_json"],
        c["wrong_keys"],
        c["bad_intent"],
        c["bad_slot"],
        c["duplicates"],
        c["kept"],
        checksum,
    )
    print(json.dumps({"counts": c, "sha256": checksum, "train": str(out_path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
