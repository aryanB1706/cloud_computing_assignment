#!/usr/bin/env python3
"""Remove invalid teacher replies from shard objects so a resumable worker can
re-label only the missing ids.

This is a development tool for a run whose teacher occasionally emitted extra
text.  The production cleaner still records and rejects bad replies; this tool
simply makes the resumable worker retry them.

    STORAGE_BACKEND=local LOCAL_STORAGE_DIR=local-storage \
      python scripts/filter_invalid_shards.py
    python scripts/filter_invalid_shards.py --dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "worker"))

from clean import load_schema, validate_reply  # noqa: E402
from storage import DATASETS_BUCKET, get_store, jsonl_bytes, read_jsonl  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", default="teacher/")
    parser.add_argument("--schema", default="data/schema.json")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    schema = load_schema(args.schema)
    intents = set(schema["intents"])
    store = get_store()
    keys = sorted(k for k in store.list(DATASETS_BUCKET, args.prefix) if k.endswith(".jsonl"))
    if not keys:
        raise SystemExit(f"no shard objects under {args.prefix}")

    total_dropped = 0
    for key in keys:
        rows = read_jsonl(store.get_bytes(DATASETS_BUCKET, key) or b"")
        kept, dropped = [], []
        for row in rows:
            reason, _ = validate_reply(
                row.get("teacher_raw"), str(row.get("text", "")), intents
            )
            (dropped if reason else kept).append(row)
        total_dropped += len(dropped)
        print(
            f"{key}: kept={len(kept)} dropped={len(dropped)} "
            f"ids={[r.get('id') for r in dropped][:20]}"
        )
        if not args.dry_run and dropped:
            store.put_bytes(DATASETS_BUCKET, key, jsonl_bytes(kept))
    print(f"total dropped: {total_dropped}" + (" (dry run, nothing written)" if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
